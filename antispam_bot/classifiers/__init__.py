"""Pluggable spam classifier backends.

Add a new backend by implementing :class:`Classifier` and registering it in
``_BACKENDS`` below. Select one via the ``CLASSIFIER_BACKEND`` env variable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import Config
from .anthropic import AnthropicClassifier
from .base import (
    ClassificationContext,
    Classifier,
    ClassifyResult,
    Verdict,
    parse_verdict_json,
)
from .heuristic import HeuristicClassifier
from .ollama import OllamaClassifier
from .openai_compat import OpenAICompatClassifier

if TYPE_CHECKING:
    from ..runtime_settings import Settings

__all__ = [
    "Classifier",
    "ClassificationContext",
    "ClassifyResult",
    "Verdict",
    "parse_verdict_json",
    "build_classifier",
    "AVAILABLE_BACKENDS",
]

_BACKENDS: dict[str, type[Classifier]] = {
    "openai_compat": OpenAICompatClassifier,
    "anthropic": AnthropicClassifier,
    "ollama": OllamaClassifier,
    "heuristic": HeuristicClassifier,
}

AVAILABLE_BACKENDS = tuple(_BACKENDS)


def build_classifier(config: Config, settings: Settings) -> Classifier:
    backend = config.classifier_backend.lower()
    try:
        cls = _BACKENDS[backend]
    except KeyError:
        raise ValueError(
            f"Unknown CLASSIFIER_BACKEND={config.classifier_backend!r}. "
            f"Available: {', '.join(AVAILABLE_BACKENDS)}"
        ) from None
    return cls(config, settings)
