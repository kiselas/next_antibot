"""Zero-cost rule-based classifier (no external API).

Useful as a fully offline backend or a cheap default. Scores a few well-known
spam signals; the final ban decision still respects ``spam_confidence_threshold``.
"""

from __future__ import annotations

import re

from .base import ClassificationContext, Classifier, ClassifyResult, Verdict

_SPAM_KEYWORDS = (
    # English
    "crypto",
    "bitcoin",
    "airdrop",
    "nft",
    "casino",
    "betting",
    "investment",
    "earn money",
    "make money",
    "work from home",
    "giveaway",
    "promo code",
    "dm me",
    "viagra",
    "loan",
    "forex",
    "trading signals",
    # Russian
    "крипт",
    "биткоин",
    "заработок",
    "заработат",
    "казино",
    "ставки",
    "инвест",
    "пиши в личк",
    "в лс",
    "скидка",
    "розыгрыш",
    "промокод",
    "займ",
    "кредит",
    "удалённая работа",
    "удаленная работа",
    "доход",
)
_INVITE_RE = re.compile(r"(t\.me/|telegram\.me/|joinchat|t\.me/\+)", re.IGNORECASE)
_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)

_REASON = {
    "en": "rule match: {signals}",
    "ru": "сработали правила: {signals}",
}


class HeuristicClassifier(Classifier):
    name = "heuristic"

    async def classify(self, ctx: ClassificationContext) -> ClassifyResult:
        lowered = ctx.text.lower()
        score = 0.0
        signals: list[str] = []

        has_url = ctx.has_links or bool(_URL_RE.search(ctx.text))
        if _INVITE_RE.search(ctx.text):
            score += 0.5
            signals.append("invite-link")

        matched = [kw for kw in _SPAM_KEYWORDS if kw in lowered]
        if matched:
            score += min(0.6, 0.2 * len(matched))
            signals.append("keywords:" + ",".join(matched[:3]))
            if has_url:
                score += 0.2
                signals.append("url+keyword")

        if (ctx.has_mentions or ctx.is_forward) and has_url:
            score += 0.2
            signals.append("mention/forward+url")

        score = min(1.0, round(score, 2))
        reason = (
            _REASON.get(ctx.lang, _REASON["en"]).format(signals=", ".join(signals))
            if signals
            else ""
        )
        verdict = Verdict(is_spam=score >= 0.5, confidence=score, reason=reason)
        return ClassifyResult(verdict, self.name, None)
