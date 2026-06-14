# Antispam Bot

A self-hosted Telegram antispam bot (Python 3.12+ / python-telegram-bot v22).
It classifies messages from new members with a **pluggable classifier backend**
(LLM via any OpenAI-compatible API, native Anthropic, local Ollama, or a zero-cost
rule-based heuristic) and, on a spam verdict, deletes the message and applies a
ban / mute / report action. Management, statistics and false-positive recovery
happen in the bot's DM.

> Languages: bot messages are available in **English (default)** and **Russian**,
> switchable at runtime with `/set language ru`.

## How it works

```
Message in a group
      │
      ▼
Chat allowed? (ALLOWED_CHAT_IDS) ── no ─► ignore (bot leaves foreign chats)
      │ yes
      ▼
Author trusted / admin / whitelisted? ── yes ─► skip
      │ no
      ▼
Pre-filter (short & no links?) ── yes ─► skip (saves quota)
      │ no
      ▼
Daily budget left? (llm_daily_limit) ── no ─► skip + alert (fail-safe)
      │ yes
      ▼
Classifier backend ─► {is_spam, confidence, reason}
      │
      ├─ spam, confidence ≥ threshold ─► delete + action (ban/mute/report) + report
      ├─ not spam ─► +1 clean; after N messages the author becomes trusted
      └─ error ─► fail-safe: do nothing; alert admin on repeated failures
```

- **Trust model.** A newcomer is checked until they accumulate
  `trust_after_clean_msgs` clean messages or stay `trust_after_hours` in the group.
  Admins and whitelisted users are trusted immediately.
- **Fail-safe.** If the classifier is unavailable, no sanction is applied.
- **Prompt-injection guard.** Message text is passed to the model as untrusted data
  inside markers; instructions inside the text are ignored.
- **Abuse hardening.** Chat allowlist with auto-leave, per-day classifier budget,
  callback-button authorization.

## Classifier backends

Select with `CLASSIFIER_BACKEND`:

| Backend | `CLASSIFIER_BACKEND` | Notes |
|---|---|---|
| OpenAI-compatible | `openai_compat` (default) | OpenRouter, OpenAI, Together, Groq, vLLM, LM Studio, … — set `LLM_BASE_URL` |
| Anthropic | `anthropic` | Native Messages API; set `LLM_BASE_URL=https://api.anthropic.com` |
| Ollama | `ollama` | Local models; set `LLM_BASE_URL=http://localhost:11434` |
| Heuristic | `heuristic` | Offline rule-based, no API key, zero cost |

Adding a backend = implement `Classifier` in `antispam_bot/classifiers/` and register
it in `classifiers/__init__.py`.

## Setup

1. Create a bot via [@BotFather](https://t.me/BotFather), get `BOT_TOKEN`.
2. **Disable Privacy Mode:** BotFather → `/mybots` → bot → *Bot Settings* →
   *Group Privacy* → **Turn off** (otherwise the bot can't see normal messages).
3. Add the bot to the group as an **administrator** with **delete messages** and
   **ban users** rights.
4. Pick a backend and provide its credentials (for OpenRouter: a key from
   <https://openrouter.ai/keys>).
5. Put your group's id in `ALLOWED_CHAT_IDS` and your `@username`/id in
   `ADMIN_USERNAMES` / `ADMIN_USER_IDS`.

## Run

```bash
cp .env.example .env   # then fill it in
docker compose up -d --build
docker compose logs -f
```

The SQLite DB is stored in `./data` (volume) and survives restarts. A container
healthcheck (heartbeat file) reports status in `docker ps`.

### Local / without Docker

```bash
pip install -e .            # or: pip install .
# set DB_PATH=./data/bot.db in .env for local runs
antispam-bot               # or: python -m antispam_bot
```

## Management (in the bot's DM, admins only)

| Command | Action |
|---|---|
| `/stats` | Moderation statistics |
| `/recent [N]` | Last N actions with reason and text |
| `/test <text>` | Run the classifier on text with no side effects |
| `/config` | Current parameters |
| `/set <key> <value>` | Change a parameter at runtime |
| `/unban <user_id> [chat_id]` | Lift a ban/mute and mark trusted |
| `/allow` `/unallow` `/whitelist` | Manage the whitelist |
| `/resetstats` | Reset statistics |

If `ADMIN_CHAT_ID` is set, each action is reported there with inline buttons
(**Unban/Unmute**, **OK**, or **Ban** in report mode) for one-tap correction.

Runtime parameters (`/set`): `enabled`, `language`, `action_mode` (ban/mute/report),
`spam_confidence_threshold`, `trust_after_clean_msgs`, `trust_after_hours`,
`min_chars_for_llm`, `max_chars_to_llm`, `llm_daily_limit`, `group_topic`,
`allowed_domains`, `model`, `use_json_format`.

## Development

```bash
pip install -e ".[dev]"
ruff check antispam_bot tests
mypy antispam_bot
pytest --cov            # 100% coverage
pre-commit install      # optional: run ruff+mypy on commit
```

## Limitations

- Classification uses **text/captions only**; image-only messages aren't analyzed.
- On OpenRouter's strictly-free tier the cap is 50 requests/day (1000/day after a
  one-time $10 top-up).
- `trust_after_hours` counts from when the bot first saw the user.

## Project layout

```
antispam_bot/
  main.py             entry point, application builder, polling
  config.py           env config (pydantic-settings)
  i18n.py             message catalogs (en/ru)
  storage.py          SQLite: trust, action log, stats, settings, whitelist
  runtime_settings.py dynamic parameters (/set)
  pipeline.py         pre-filter -> classifier -> decision
  handlers.py         moderation, membership, admin commands, callbacks
  classifiers/        pluggable backends (base, openai_compat, anthropic, ollama, heuristic)
tests/                unit + integration tests (100% coverage)
```

## Contributing & security

- [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) ·
  [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)

## License

[MIT](LICENSE) © 2026 Aleksandr
