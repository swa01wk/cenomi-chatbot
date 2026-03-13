"""
Tenant parameter service — load, merge, apply, and update tenant config.

Merge order (later wins):
  1. Global defaults  (baked into TenantConfig Pydantic defaults)
  2. Tenant defaults   (from config file: tenant_defaults.json)
  3. Mall overrides    (from config file: cenomi_mall_01.json)
  4. Session overrides (runtime, per-session, validated against mutability)

This module is the single gateway between config files and the runtime
pipeline.  No other code should parse raw tenant config JSON.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.models.tenant import (
    Mutability,
    TenantConfig,
)

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "tenant_config"


# ═══════════════════════════════════════════════════════════════════════════
# Loading
# ═══════════════════════════════════════════════════════════════════════════


def load_tenant_config(
    mall_id: str = "cenomi_mall_01",
    config_dir: Path | None = None,
) -> TenantConfig:
    """
    Build a TenantConfig by layering:
      global defaults → tenant defaults → mall-specific overrides.
    """
    base_dir = config_dir or _CONFIG_DIR

    defaults_path = base_dir / "tenant_defaults.json"
    mall_path = base_dir / f"{mall_id}.json"

    base: dict[str, Any] = {}

    if defaults_path.exists():
        base = _load_json(defaults_path)
        logger.info("Loaded tenant defaults from %s", defaults_path)

    if mall_path.exists():
        overrides = _load_json(mall_path)
        base = deep_merge(base, overrides)
        logger.info("Applied mall overrides from %s", mall_path)

    base.setdefault("mall_id", mall_id)
    return TenantConfig(**base)


# ═══════════════════════════════════════════════════════════════════════════
# Deep merge
# ═══════════════════════════════════════════════════════════════════════════


def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """
    Recursively merge *overrides* into *base*.
    Dict values are merged recursively; all other types are replaced.
    """
    result = deepcopy(base)
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Session-level overrides (runtime)
# ═══════════════════════════════════════════════════════════════════════════


def apply_session_overrides(
    config: TenantConfig,
    overrides: dict[str, dict[str, Any]],
) -> TenantConfig:
    """
    Apply session-level overrides to a TenantConfig, respecting mutability.

    *overrides* is a dict like:
        {"tone": {"warmth": 0.9}, "ranking": {"favor_budget": 0.8}}

    Only parameters with SESSION mutability are accepted.
    Others are silently dropped with a warning log.
    """
    mutability_map = config.get_mutability_map()
    patched = config.model_copy(deep=True)

    for group, params in overrides.items():
        group_mut = mutability_map.get(group, {})
        section = getattr(patched, group, None)
        if section is None:
            logger.warning("Session override for unknown group '%s' — skipping", group)
            continue

        for key, value in params.items():
            allowed = group_mut.get(key)
            if allowed != Mutability.SESSION:
                logger.warning(
                    "Session override rejected: %s.%s is %s, not session-tunable",
                    group,
                    key,
                    allowed,
                )
                continue
            if hasattr(section, key):
                setattr(section, key, value)
                logger.debug("Session override applied: %s.%s = %s", group, key, value)

    return patched


def apply_feedback_update(
    config: TenantConfig,
    group: str,
    key: str,
    value: Any,
    *,
    caller_tier: Mutability = Mutability.TENANT,
) -> TenantConfig:
    """
    Apply a single feedback-driven parameter update.

    The update is accepted only if the parameter's mutability tier is
    at or below *caller_tier* in the hierarchy:
        SESSION < TENANT < ADMIN_ONLY < STATIC

    Returns a new TenantConfig with the update applied, or the
    original config unchanged if the update is rejected.
    """
    hierarchy = [Mutability.SESSION, Mutability.TENANT, Mutability.ADMIN_ONLY, Mutability.STATIC]
    mutability_map = config.get_mutability_map()
    param_mut = mutability_map.get(group, {}).get(key)

    if param_mut is None:
        logger.warning("Feedback update for unknown param %s.%s — rejected", group, key)
        return config

    if hierarchy.index(param_mut) > hierarchy.index(caller_tier):
        logger.warning(
            "Feedback update rejected: %s.%s requires %s, caller has %s",
            group,
            key,
            param_mut.value,
            caller_tier.value,
        )
        return config

    patched = config.model_copy(deep=True)
    section = getattr(patched, group, None)
    if section and hasattr(section, key):
        setattr(section, key, value)
        logger.info("Feedback update applied: %s.%s = %s", group, key, value)

    return patched


# ═══════════════════════════════════════════════════════════════════════════
# Serialization
# ═══════════════════════════════════════════════════════════════════════════


def export_config(config: TenantConfig, path: Path | None = None) -> dict[str, Any]:
    """Serialize a TenantConfig to a dict (and optionally save to disk)."""
    data = config.model_dump(mode="json")
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.info("Exported tenant config to %s", path)
    return data


def diff_from_defaults(config: TenantConfig) -> dict[str, Any]:
    """
    Return only the parameters that differ from global defaults.
    Useful for generating minimal override files.
    """
    defaults = TenantConfig(mall_id=config.mall_id)
    defaults_dict = defaults.model_dump(mode="json")
    current_dict = config.model_dump(mode="json")
    return _dict_diff(defaults_dict, current_dict)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dict_diff(base: Any, current: Any) -> Any:
    """Recursively compute the diff between two dicts."""
    if isinstance(base, dict) and isinstance(current, dict):
        diff: dict[str, Any] = {}
        for key in current:
            if key not in base:
                diff[key] = current[key]
            else:
                sub = _dict_diff(base[key], current[key])
                if sub is not None:
                    diff[key] = sub
        return diff if diff else None
    if base != current:
        return current
    return None
