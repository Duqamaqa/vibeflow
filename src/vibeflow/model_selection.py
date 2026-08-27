"""Resolve logical tiers against the models FCC actually exposes."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence
import tomllib


class ModelSelectionError(RuntimeError):
    """Raised when a configured tier has no usable live model."""


STRONG_OPENAI_PREFERENCES: tuple[str, ...] = (
    "gpt-5.6-sol",
    "gpt-5.6",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex",
    "gpt-5-codex",
)


def extract_model_ids(payload: Any) -> tuple[str, ...]:
    """Extract model IDs from common OpenAI/FCC catalog shapes."""

    if not isinstance(payload, Mapping):
        return ()
    collection = payload.get("data", payload.get("models", ()))
    if isinstance(collection, Mapping):
        collection = collection.values()
    if not isinstance(collection, (list, tuple, set)):
        return ()
    identifiers: list[str] = []
    for item in collection:
        if isinstance(item, str):
            identifiers.append(item)
            continue
        if not isinstance(item, Mapping):
            continue
        identifier = next(
            (
                item.get(key)
                for key in ("id", "slug", "model")
                if isinstance(item.get(key), str) and item.get(key).strip()
            ),
            None,
        )
        if identifier:
            identifiers.append(str(identifier).strip())
    return tuple(dict.fromkeys(identifiers))


def load_routing_preferences(path: str | Path) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    config_path = Path(path)
    if not config_path.is_file():
        raise ModelSelectionError(f"Routing config is missing: {config_path}")
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ModelSelectionError(f"Cannot read routing config: {exc}") from exc
    tiers = config.get("tiers", {})
    candidates = config.get("candidates", {})
    preferred: dict[str, str] = {}
    alternatives: dict[str, tuple[str, ...]] = {}
    for tier in ("cheap", "standard", "strong"):
        tier_value = tiers.get(tier, {}) if isinstance(tiers, Mapping) else {}
        model = tier_value.get("model") if isinstance(tier_value, Mapping) else tier_value
        if isinstance(model, str) and model.strip():
            preferred[tier] = model.strip()
        raw_alternatives = candidates.get(tier, ()) if isinstance(candidates, Mapping) else ()
        if isinstance(raw_alternatives, list):
            alternatives[tier] = tuple(
                str(item).strip() for item in raw_alternatives if isinstance(item, str) and item.strip()
            )
        else:
            alternatives[tier] = ()
    return preferred, alternatives


def resolve_tier_models(
    routing_path: str | Path,
    live_payload: Any,
) -> dict[str, str]:
    """Choose the first configured live match, with ranked OpenAI strong discovery."""

    preferred, alternatives = load_routing_preferences(routing_path)
    available = extract_model_ids(live_payload)
    if not available:
        raise ModelSelectionError("FCC returned no usable model IDs")
    resolved: dict[str, str] = {}
    for tier in ("cheap", "standard", "strong"):
        configured = preferred.get(tier)
        choices = tuple(item for item in (configured, *alternatives.get(tier, ())) if item)
        if configured and configured.startswith("auto:"):
            match = _rank_openai_codex(available)
        else:
            match = next((_catalog_match(choice, available) for choice in choices if _catalog_match(choice, available)), None)
        if match is None:
            raise ModelSelectionError(
                f"No live FCC model matches the configured {tier} tier: {', '.join(choices) or '(none)'}"
            )
        resolved[tier] = match
    return resolved


def _catalog_match(choice: str, available: Sequence[str]) -> str | None:
    if _is_claude_model_id(choice):
        return None
    if choice in available and not _is_claude_model_id(choice):
        return choice
    normalized = choice.lower().strip("/")
    suffix_matches = [
        item for item in available
        if item.lower().strip("/").endswith(normalized)
        and not _is_claude_model_id(item)
    ]
    if not suffix_matches:
        return None
    return sorted(suffix_matches, key=_transport_rank)[0]


def _rank_openai_codex(available: Sequence[str]) -> str | None:
    openai_models = [
        model for model in available
        if _is_openai_model_id(model) and not _is_claude_model_id(model)
    ]
    for preference in STRONG_OPENAI_PREFERENCES:
        matches = [model for model in openai_models if preference in model.lower()]
        if matches:
            return sorted(matches, key=_openai_rank)[0]
    codex_models = [model for model in openai_models if "codex" in model.lower()]
    return sorted(codex_models, key=_openai_rank)[0] if codex_models else None


def _is_claude_model_id(model: str) -> bool:
    normalized = model.lower().strip("/")
    return "claude" in normalized or "/~anthropic/" in f"/{normalized}/"


def _is_openai_model_id(model: str) -> bool:
    normalized = model.lower().strip("/")
    return normalized.startswith("openai/") or "/openai/" in f"/{normalized}/"


def _transport_rank(model: str) -> tuple[int, int, str]:
    normalized = model.lower().strip("/")
    wrapper_rank = 1 if normalized.startswith("anthropic/") else 0
    return wrapper_rank, len(normalized), normalized


def _openai_rank(model: str) -> tuple[int, int, str]:
    normalized = model.lower().strip("/")
    direct_provider = (
        normalized.startswith("openai/")
        or normalized.startswith("anthropic/openai/")
    )
    provider_rank = 0 if direct_provider else 1
    return provider_rank, *_transport_rank(normalized)[1:]
