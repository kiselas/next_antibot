"""Messenger-agnostic moderation core.

Operates on normalized events and a :class:`BotPlatform`; knows nothing about
any specific messenger SDK.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import Any

from .classifiers import Classifier
from .config import Config
from .i18n import t
from .pipeline import Decision, MessageMeta, Result, evaluate
from .platform import (
    BotMembership,
    BotPlatform,
    Button,
    CallbackAction,
    CommandRequest,
    IncomingMessage,
    MemberUpdate,
    User,
)
from .runtime_settings import Settings
from .storage import Storage

log = logging.getLogger(__name__)


def parse_user_ref(ref: str) -> tuple[int | None, str | None]:
    """Parse ``@name`` / ``name`` / ``123`` into (user_id, username)."""
    ref = ref.strip()
    if ref.startswith("@"):
        return None, ref[1:]
    try:
        return int(ref), None
    except ValueError:
        return None, ref


class Core:
    def __init__(
        self,
        config: Config,
        storage: Storage,
        settings: Settings,
        classifier: Classifier,
        platform: BotPlatform,
        *,
        started_at: float | None = None,
    ) -> None:
        self.config = config
        self.storage = storage
        self.settings = settings
        self.classifier = classifier
        self.platform = platform
        self.started_at = started_at if started_at is not None else time.time()
        self._llm_streak = 0
        self._llm_alerted = False
        self._budget: dict[int, dict[str, Any]] = {}  # per-chat daily counters

    # ---- small helpers ----
    @property
    def lang(self) -> str:
        return str(self.settings.get("language"))

    def _t(self, _key: str, **kw: object) -> str:
        return t(_key, self.lang, **kw)

    def is_bot_admin(self, user: User | None) -> bool:
        if user is None:
            return False
        if user.id in self.config.admin_user_ids:
            return True
        return bool(user.username and user.username.lower() in self.config.admin_usernames)

    def chat_allowed(self, chat_id: int) -> bool:
        return not self.config.allowed_chat_ids or chat_id in self.config.allowed_chat_ids

    async def _is_group_admin(self, chat_id: int, user_id: int) -> bool:
        return user_id in await self.platform.chat_admin_ids(chat_id)

    # ---- message moderation ----
    async def on_message(self, msg: IncomingMessage) -> None:
        if not msg.is_group or msg.user.is_bot or msg.is_automatic:
            return
        if not self.chat_allowed(msg.chat_id):
            return
        await self.settings.ensure_loaded(msg.chat_id)
        if not bool(self.settings.get("enabled", msg.chat_id)):
            return

        rec = await self.storage.get_user(msg.chat_id, msg.user.id)
        if rec is not None and rec["status"] == "trusted":
            return
        if await self.storage.is_whitelisted(msg.user.id, msg.user.username):
            return
        if self.is_bot_admin(msg.user) or await self._is_group_admin(msg.chat_id, msg.user.id):
            await self.storage.ensure_user(msg.chat_id, msg.user.id, msg.user.username)
            await self.storage.set_status(msg.chat_id, msg.user.id, "trusted")
            return

        rec = await self.storage.ensure_user(msg.chat_id, msg.user.id, msg.user.username)
        trust_hours = int(self.settings.get("trust_after_hours", msg.chat_id))
        if trust_hours > 0 and (time.time() - float(rec["first_seen"])) >= trust_hours * 3600:
            await self.storage.set_status(msg.chat_id, msg.user.id, "trusted")
            return

        if not msg.text.strip():
            return

        if not self._budget_consume(msg.chat_id):
            await self.storage.incr_stat("llm_budget_skipped")
            await self._on_budget_exceeded(msg.chat_id)
            return

        meta = MessageMeta(
            has_links=msg.has_links, has_mentions=msg.has_mentions, is_forward=msg.is_forward
        )
        result = await evaluate(
            msg.text, meta, settings=self.settings, classifier=self.classifier, chat_id=msg.chat_id
        )

        if result.decision in (Decision.CLEAN, Decision.SPAM):
            await self.storage.incr_stat("messages_checked")
            self._reset_llm_streak()

        if result.decision is Decision.SPAM:
            await self._act_on_spam(msg, result)
        elif result.decision is Decision.CLEAN:
            count = await self.storage.increment_clean(msg.chat_id, msg.user.id)
            threshold = int(self.settings.get("trust_after_clean_msgs", msg.chat_id))
            if threshold > 0 and count >= threshold:
                await self.storage.set_status(msg.chat_id, msg.user.id, "trusted")
        elif result.decision is Decision.ERROR:
            await self.storage.incr_stat("llm_errors")
            await self._on_llm_error(result.error)
        else:  # SKIP
            await self.storage.incr_stat("messages_skipped_prefilter")

    async def _act_on_spam(self, msg: IncomingMessage, result: Result) -> None:
        mode = str(self.settings.get("action_mode", msg.chat_id))
        try:
            await self.platform.delete_message(msg.chat_id, msg.message_id)
        except Exception as exc:
            log.warning("Could not delete message: %s", exc)

        sanctioned = False
        try:
            if mode == "ban":
                await self.platform.ban_user(msg.chat_id, msg.user.id)
                sanctioned = True
            elif mode == "mute":
                await self.platform.mute_user(msg.chat_id, msg.user.id)
                sanctioned = True
        except Exception as exc:
            log.warning("Could not %s %s: %s", mode, msg.user.id, exc)

        await self.storage.record_ban(
            msg.chat_id,
            msg.user.id,
            msg.user.username,
            mode,
            result.reason,
            result.confidence,
            result.model,
            msg.text,
        )
        await self.storage.incr_stat("spam_detected")
        if sanctioned:
            await self.storage.incr_stat("users_banned")
        log.info(
            "SPAM [%s]: user=%s (@%s) chat=%s conf=%.2f model=%s reason=%s",
            mode,
            msg.user.id,
            msg.user.username,
            msg.chat_id,
            result.confidence,
            result.model,
            result.reason,
        )

        if self.config.admin_chat_id:
            uname = f"@{msg.user.username}" if msg.user.username else f"id{msg.user.id}"
            preview = msg.text[:300] + ("…" if len(msg.text) > 300 else "")
            report = self._t(
                "report",
                action=self._t(f"report_action_{mode}"),
                uname=uname,
                user_id=msg.user.id,
                chat=msg.chat_title or msg.chat_id,
                conf=result.confidence,
                model=result.model,
                reason=result.reason,
                preview=preview,
            )
            try:
                await self.platform.send_message(
                    self.config.admin_chat_id,
                    report,
                    buttons=self._spam_buttons(mode, msg.chat_id, msg.user.id),
                )
            except Exception as exc:
                log.warning("Could not send report to admin chat: %s", exc)

    def _spam_buttons(self, mode: str, chat_id: int, user_id: int) -> list[list[Button]]:
        def cd(action: str) -> str:
            return f"{action}:{chat_id}:{user_id}"

        if mode == "report":
            return [[(self._t("btn_ban"), cd("ban")), (self._t("btn_ignore"), cd("ok"))]]
        unban = self._t("btn_unmute") if mode == "mute" else self._t("btn_unban")
        return [[(unban, cd("unban")), (self._t("btn_ok"), cd("ok"))]]

    # ---- daily budget (per chat) ----
    def _budget_consume(self, chat_id: int) -> bool:
        limit = int(self.settings.get("llm_daily_limit", chat_id))
        if limit <= 0:
            return True
        today = time.strftime("%Y-%m-%d")
        b = self._budget.get(chat_id)
        if b is None or b["day"] != today:
            b = {"day": today, "count": 0, "alerted": False}
            self._budget[chat_id] = b
        if int(b["count"]) >= limit:
            return False
        b["count"] = int(b["count"]) + 1
        return True

    async def _on_budget_exceeded(self, chat_id: int) -> None:
        log.warning("Daily classifier limit reached for chat %s (fail-safe).", chat_id)
        b = self._budget.setdefault(chat_id, {"day": "", "count": 0, "alerted": False})
        if self.config.admin_chat_id and not b.get("alerted"):
            b["alerted"] = True
            await self._safe_send(
                self.config.admin_chat_id,
                self._t("budget_alert", limit=self.settings.get("llm_daily_limit", chat_id)),
            )

    # ---- classifier failure alerting ----
    def _reset_llm_streak(self) -> None:
        self._llm_streak = 0
        self._llm_alerted = False

    async def _on_llm_error(self, error: str | None) -> None:
        self._llm_streak += 1
        log.warning(
            "Classifier unavailable (%s); message left in place. Streak: %d",
            error,
            self._llm_streak,
        )
        if (
            self.config.admin_chat_id
            and self._llm_streak >= self.config.llm_error_alert_threshold
            and not self._llm_alerted
        ):
            self._llm_alerted = True
            label = self._t(f"err_{error}") if error else self._t("err_unavailable")
            await self._safe_send(
                self.config.admin_chat_id,
                self._t("llm_alert", label=label, streak=self._llm_streak),
            )

    async def _safe_send(self, chat_id: int, text: str) -> None:
        try:
            await self.platform.send_message(chat_id, text)
        except Exception as exc:
            log.warning("Could not send message to %s: %s", chat_id, exc)

    # ---- membership / presence ----
    async def on_member(self, m: MemberUpdate) -> None:
        if not self.chat_allowed(m.chat_id) or m.user.is_bot or not m.joined:
            return
        await self.storage.ensure_user(m.chat_id, m.user.id, m.user.username)
        await self.storage.incr_stat("members_joined")
        log.info("New member: %s (@%s) in chat %s", m.user.id, m.user.username, m.chat_id)

    async def on_bot_membership(self, b: BotMembership) -> None:
        if not b.present:
            return
        if not self.chat_allowed(b.chat_id):
            log.warning("Added to a non-allowed chat %s (%s) — leaving.", b.chat_id, b.chat_title)
            try:
                await self.platform.leave_chat(b.chat_id)
            except Exception as exc:
                log.warning("Could not leave chat %s: %s", b.chat_id, exc)
        else:
            log.info("Bot added to chat %s (%s)", b.chat_id, b.chat_title)

    # ---- callbacks ----
    async def on_callback(self, cb: CallbackAction) -> None:
        parts = cb.data.split(":")
        if len(parts) != 3:
            await self.platform.answer_callback(cb.callback_id)
            return
        action, chat_s, user_s = parts
        try:
            chat_id, user_id = int(chat_s), int(user_s)
        except ValueError:
            await self.platform.answer_callback(cb.callback_id, self._t("cb_bad_data"), alert=True)
            return

        if not (
            self.is_bot_admin(cb.user) or cb.user.id in await self.platform.chat_admin_ids(chat_id)
        ):
            await self.platform.answer_callback(
                cb.callback_id, self._t("cb_only_admin"), alert=True
            )
            return

        who = f"@{cb.user.username}" if cb.user.username else f"id{cb.user.id}"
        try:
            if action == "unban":
                await self.platform.unban_user(chat_id, user_id)
                await self.storage.set_status(chat_id, user_id, "trusted")
                note = self._t("cb_note_unbanned", who=who)
            elif action == "ban":
                await self.platform.ban_user(chat_id, user_id)
                await self.storage.set_status(chat_id, user_id, "untrusted")
                await self.storage.incr_stat("users_banned")
                note = self._t("cb_note_banned", who=who)
            elif action == "ok":
                note = self._t("cb_note_confirmed", who=who)
            else:
                await self.platform.answer_callback(cb.callback_id)
                return
        except Exception as exc:
            await self.platform.answer_callback(
                cb.callback_id, self._t("cb_error", error=exc), alert=True
            )
            return

        await self.platform.answer_callback(cb.callback_id, self._t("cb_done"))
        if cb.chat_id is not None and cb.message_id is not None:
            try:
                await self.platform.edit_message(
                    cb.chat_id, cb.message_id, (cb.message_text or "") + "\n\n" + note
                )
            except Exception as exc:
                log.debug("Could not update report: %s", exc)

    # ---- admin commands ----
    async def _ensure_admin(self, req: CommandRequest) -> bool:
        if not self.is_bot_admin(req.user):
            await self.platform.send_message(req.chat_id, self._t("admin_only"))
            return False
        return True

    async def cmd_start(self, req: CommandRequest) -> None:
        key = "help" if self.is_bot_admin(req.user) else "start_user"
        await self.platform.send_message(req.chat_id, self._t(key))

    async def cmd_help(self, req: CommandRequest) -> None:
        if await self._ensure_admin(req):
            await self.platform.send_message(req.chat_id, self._t("help"))

    async def cmd_stats(self, req: CommandRequest) -> None:
        if not await self._ensure_admin(req):
            return
        stats = await self.storage.all_stats()
        users = await self.storage.user_counts()
        bans_24h = await self.storage.bans_since(time.time() - 86400)
        text = self._t(
            "stats",
            uptime=(time.time() - self.started_at) / 3600,
            checked=stats.get("messages_checked", 0),
            skipped=stats.get("messages_skipped_prefilter", 0),
            spam=stats.get("spam_detected", 0),
            banned=stats.get("users_banned", 0),
            actions_24h=bans_24h,
            llm_errors=stats.get("llm_errors", 0),
            budget_skipped=stats.get("llm_budget_skipped", 0),
            joined=stats.get("members_joined", 0),
            trusted=users.get("trusted", 0),
            pending=users.get("untrusted", 0),
            total=users.get("total", 0),
        )
        await self.platform.send_message(req.chat_id, text)

    async def cmd_recent(self, req: CommandRequest) -> None:
        if not await self._ensure_admin(req):
            return
        limit = 10
        if req.args:
            with contextlib.suppress(ValueError):
                limit = max(1, min(50, int(req.args[0])))
        rows = await self.storage.recent_bans(limit)
        if not rows:
            await self.platform.send_message(req.chat_id, self._t("recent_empty"))
            return
        lines = [self._t("recent_header", count=len(rows))]
        for r in rows:
            when = time.strftime("%d.%m %H:%M", time.localtime(r["ts"]))
            uname = f"@{r['username']}" if r["username"] else f"id{r['user_id']}"
            text = (r["message_text"] or "").replace("\n", " ")[:120]
            lines.append(
                self._t(
                    "recent_item",
                    when=when,
                    action=r["action"],
                    uname=uname,
                    user_id=r["user_id"],
                    conf=r["confidence"],
                    reason=r["reason"],
                    text=text,
                )
            )
        await self.platform.send_message(req.chat_id, "\n".join(lines))

    async def cmd_test(self, req: CommandRequest) -> None:
        if not await self._ensure_admin(req):
            return
        text = " ".join(req.args).strip()
        if not text:
            await self.platform.send_message(req.chat_id, self._t("test_usage"))
            return
        meta = MessageMeta(
            has_links=("http://" in text or "https://" in text),
            has_mentions=("@" in text),
            is_forward=False,
        )
        result = await evaluate(text, meta, settings=self.settings, classifier=self.classifier)
        body = self._t(
            "test_result",
            decision=result.decision.value,
            conf=result.confidence,
            reason=result.reason or "—",
            model=result.model or "—",
        )
        if result.error:
            body += self._t("test_error_suffix", error=self._t(f"err_{result.error}"))
        await self.platform.send_message(req.chat_id, body)

    async def cmd_config(self, req: CommandRequest) -> None:
        if not await self._ensure_admin(req):
            return
        chat_id = 0
        if req.args:
            try:
                chat_id = int(req.args[0])
            except ValueError:
                chat_id = 0
        await self.settings.ensure_loaded(chat_id)
        values = self.settings.all(chat_id)
        header = (
            self._t("config_header")
            if not chat_id
            else self._t("config_header_chat", chat_id=chat_id)
        )
        lines = [header]
        for key, desc in self.settings.describe().items():
            shown = values[key] if values[key] != "" else "—"
            lines.append(self._t("config_item", key=key, value=shown, desc=desc))
        await self.platform.send_message(req.chat_id, "\n".join(lines))

    async def _apply_set(
        self, req: CommandRequest, key: str, raw: str, chat_id: int
    ) -> object | None:
        """Validate and persist a setting; reply on error. Returns value or None."""
        try:
            return await self.settings.set(key, raw, chat_id)
        except KeyError:
            await self.platform.send_message(
                req.chat_id, self._t("set_unknown", key=key, known=", ".join(self.settings.SPEC))
            )
        except ValueError as exc:
            await self.platform.send_message(req.chat_id, self._t("set_invalid", error=exc))
        return None

    async def cmd_set(self, req: CommandRequest) -> None:
        if not await self._ensure_admin(req):
            return
        if len(req.args) < 2:
            await self.platform.send_message(req.chat_id, self._t("set_usage"))
            return
        key, raw = req.args[0].lower(), " ".join(req.args[1:])
        value = await self._apply_set(req, key, raw, 0)
        if value is not None:
            await self.platform.send_message(req.chat_id, self._t("set_ok", key=key, value=value))

    async def cmd_setchat(self, req: CommandRequest) -> None:
        if not await self._ensure_admin(req):
            return
        if len(req.args) < 3:
            await self.platform.send_message(req.chat_id, self._t("setchat_usage"))
            return
        try:
            chat_id = int(req.args[0])
        except ValueError:
            await self.platform.send_message(req.chat_id, self._t("setchat_chat_num"))
            return
        key, raw = req.args[1].lower(), " ".join(req.args[2:])
        value = await self._apply_set(req, key, raw, chat_id)
        if value is not None:
            await self.platform.send_message(
                req.chat_id, self._t("setchat_ok", key=key, value=value, chat_id=chat_id)
            )

    async def cmd_unban(self, req: CommandRequest) -> None:
        if not await self._ensure_admin(req):
            return
        if not req.args:
            await self.platform.send_message(req.chat_id, self._t("unban_usage"))
            return
        try:
            user_id = int(req.args[0])
        except ValueError:
            await self.platform.send_message(req.chat_id, self._t("unban_user_id_num"))
            return

        chat_id: int | None = None
        if len(req.args) >= 2:
            try:
                chat_id = int(req.args[1])
            except ValueError:
                await self.platform.send_message(req.chat_id, self._t("unban_chat_id_num"))
                return
        else:
            chat_id = await self.storage.last_ban_chat(user_id)
            if chat_id is None and len(self.config.allowed_chat_ids) == 1:
                chat_id = self.config.allowed_chat_ids[0]
            if chat_id is None:
                await self.platform.send_message(req.chat_id, self._t("unban_no_chat"))
                return

        try:
            await self.platform.unban_user(chat_id, user_id)
        except Exception as exc:
            await self.platform.send_message(req.chat_id, self._t("unban_fail", error=exc))
            return
        await self.storage.set_status(chat_id, user_id, "trusted")
        await self.platform.send_message(
            req.chat_id, self._t("unban_ok", user_id=user_id, chat_id=chat_id)
        )

    async def cmd_allow(self, req: CommandRequest) -> None:
        if not await self._ensure_admin(req):
            return
        if not req.args:
            await self.platform.send_message(req.chat_id, self._t("allow_usage"))
            return
        user_id, username = parse_user_ref(req.args[0])
        added = await self.storage.add_whitelist(user_id, username)
        key = "allow_added" if added else "allow_exists"
        await self.platform.send_message(req.chat_id, self._t(key, target=req.args[0]))

    async def cmd_unallow(self, req: CommandRequest) -> None:
        if not await self._ensure_admin(req):
            return
        if not req.args:
            await self.platform.send_message(req.chat_id, self._t("unallow_usage"))
            return
        user_id, username = parse_user_ref(req.args[0])
        removed = await self.storage.remove_whitelist(user_id, username)
        key = "unallow_removed" if removed else "unallow_none"
        await self.platform.send_message(req.chat_id, self._t(key, count=removed))

    async def cmd_whitelist(self, req: CommandRequest) -> None:
        if not await self._ensure_admin(req):
            return
        rows = await self.storage.list_whitelist()
        if not rows:
            await self.platform.send_message(req.chat_id, self._t("whitelist_empty"))
            return
        lines = [self._t("whitelist_header")]
        for r in rows:
            ref = f"@{r['username']}" if r["username"] else f"id{r['user_id']}"
            lines.append(self._t("whitelist_item", ref=ref))
        await self.platform.send_message(req.chat_id, "\n".join(lines))

    async def cmd_resetstats(self, req: CommandRequest) -> None:
        if not await self._ensure_admin(req):
            return
        await self.storage.clear_stats()
        self.started_at = time.time()
        await self.platform.send_message(req.chat_id, self._t("resetstats_ok"))
