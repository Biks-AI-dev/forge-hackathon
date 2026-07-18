"""Server-side ForgeSpec validation (PRD §5 step 8). Never trusts Kimi's
output directly. Reuses the Provisioner's own ForgeSpec model and base
validation (business_name / catalogue presence) as the single source of
truth for that schema — see provisioner/app/models.py and validation.py —
then layers the stricter checks Meeting Mode specifically owns: price
sanity and cross-field conflict detection.
"""
import logging
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger("meeting-mode")

# Both provisioner/ and meeting-mode/ ship a top-level package named
# `app`. By the time this module runs, `app` in sys.modules already means
# meeting-mode's own package, so a naive `sys.path` insert + `import
# app.x` silently resolves against the wrong `app` (confirmed: raises
# ModuleNotFoundError for `app.models`). Also, provisioner/app/validation.py
# does `from .models import ...` internally — a relative import that only
# works if it's loaded as a genuine package member, ruling out a plain
# importlib.util.spec_from_file_location() load too.
#
# Fix: swap meeting-mode's `app.*` entries out of sys.modules just long
# enough to import provisioner's `app.models` / `app.validation` as real
# package members, then restore the original entries by identity —
# nothing in meeting-mode gets reloaded or re-executed.
_PROVISIONER_ROOT = Path(__file__).resolve().parent.parent.parent / "provisioner"


def _import_provisioner_forgespec_modules():
    saved = {k: v for k, v in sys.modules.items() if k == "app" or k.startswith("app.")}
    for k in saved:
        del sys.modules[k]

    sys.path.insert(0, str(_PROVISIONER_ROOT))
    try:
        from app.models import FieldError, ForgeSpec
        from app.validation import ForgeSpecValidationError, validate_forge_spec
        return FieldError, ForgeSpec, ForgeSpecValidationError, validate_forge_spec
    finally:
        sys.path.remove(str(_PROVISIONER_ROOT))
        for k in list(sys.modules):
            if k == "app" or k.startswith("app."):
                del sys.modules[k]
        sys.modules.update(saved)


FieldError, ForgeSpec, ForgeSpecValidationError, _base_validate = _import_provisioner_forgespec_modules()


def _check_prices(spec: ForgeSpec, details: list[FieldError]) -> None:
    if not spec.products:
        return
    for cat_idx, category in enumerate(spec.products.categories or []):
        for var_idx, variant in enumerate(category.variants or []):
            path = f"products.categories[{cat_idx}].variants[{var_idx}]"
            if variant.price is None:
                details.append(FieldError(field=path + ".price", message="price is required"))
            elif variant.price <= 0:
                details.append(FieldError(
                    field=path + ".price", message=f"price must be positive, got {variant.price}"
                ))


def _check_fee_rates(spec: ForgeSpec, details: list[FieldError]) -> None:
    if not spec.channels:
        return
    for idx, ch in enumerate(spec.channels):
        if ch.fee_rate is not None and not (0 <= ch.fee_rate < 1):
            details.append(FieldError(
                field=f"channels[{idx}].fee_rate",
                message=f"fee_rate must be in [0, 1), got {ch.fee_rate}",
            ))


def _check_conflicts(spec: ForgeSpec, details: list[FieldError]) -> None:
    """Detects the conflicts Meeting Mode can catch mechanically: the same
    product id or channel name declared twice with different values. The
    LLM is asked not to silently average these (see llm.py's system
    prompt); this is the deterministic backstop."""
    if spec.products:
        seen: dict[str, tuple[str, float]] = {}
        for cat_idx, category in enumerate(spec.products.categories or []):
            for var_idx, variant in enumerate(category.variants or []):
                key = variant.id
                path = f"products.categories[{cat_idx}].variants[{var_idx}]"
                if key in seen and seen[key][1] != variant.price:
                    details.append(FieldError(
                        field=path + ".id",
                        message=(
                            f"product id '{key}' appears twice with conflicting prices "
                            f"({seen[key][1]} at {seen[key][0]} vs {variant.price} here)"
                        ),
                    ))
                else:
                    seen[key] = (path, variant.price)

    if spec.channels:
        seen_ch: dict[str, tuple[int, float | None]] = {}
        for idx, ch in enumerate(spec.channels):
            key = ch.name.strip().upper()
            if key in seen_ch and seen_ch[key][1] != ch.fee_rate:
                details.append(FieldError(
                    field=f"channels[{idx}].fee_rate",
                    message=(
                        f"channel '{ch.name}' appears twice with conflicting fee_rate "
                        f"({seen_ch[key][1]} at channels[{seen_ch[key][0]}] vs {ch.fee_rate} here)"
                    ),
                ))
            else:
                seen_ch[key] = (idx, ch.fee_rate)


def _normalize_policy_placement(raw: dict) -> dict:
    """Observed live (Gate 2 rehearsal, gpt-oss-20b via Doubleword): the
    model sometimes nests `policy` inside `products` instead of at the
    top level the schema (and the system prompt) specify, leaving the
    real top-level `policy` null. That silently drops guardrails/payment
    policy — the exact thing PRD §4.3 calls "the judged differentiator,
    do not skip." Hoist a misplaced policy back to top level when the
    real top-level one is absent; the hard-fail check below still catches
    cases this can't recover (e.g. policy missing everywhere)."""
    if raw.get("policy") is None:
        for container_key in ("products", "business"):
            container = raw.get(container_key)
            if isinstance(container, dict) and container.get("policy") is not None:
                raw["policy"] = container.pop("policy")
                log.warning(
                    "normalized misplaced policy from %s.policy to top-level policy", container_key
                )
                break
    return raw


def _check_policy(spec: ForgeSpec, details: list[FieldError]) -> None:
    if spec.policy is None:
        details.append(FieldError(field="policy", message="policy is required (guardrails must not be silently absent)"))
        return
    if not spec.policy.guardrails:
        details.append(FieldError(field="policy.guardrails", message="policy.guardrails must be non-empty"))
    if spec.workflow == "sales" and not spec.policy.payment:
        details.append(FieldError(field="policy.payment", message="policy.payment is required for the sales workflow"))


def validate_generated_spec(raw: dict[str, Any]) -> ForgeSpec:
    """Raises ForgeSpecValidationError (from the Provisioner's own module,
    so callers only ever handle one exception type) with every problem
    found, not just the first."""
    raw = _normalize_policy_placement(raw)
    spec = _base_validate(raw)  # business_name / catalogue presence; raises on failure

    details: list[FieldError] = []
    _check_prices(spec, details)
    _check_fee_rates(spec, details)
    _check_conflicts(spec, details)
    _check_policy(spec, details)

    if details:
        raise ForgeSpecValidationError(details)

    return spec
