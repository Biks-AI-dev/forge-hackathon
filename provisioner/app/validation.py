from typing import Any

from pydantic import ValidationError as PydanticValidationError

from .models import FieldError, ForgeSpec


class ForgeSpecValidationError(Exception):
    def __init__(self, details: list[FieldError]):
        self.details = details
        super().__init__(f"{len(details)} field error(s)")


def validate_forge_spec(raw: dict[str, Any]) -> ForgeSpec:
    """Parse + validate an incoming ForgeSpec payload.

    Shape errors (wrong type, malformed nested object) come from Pydantic.
    Business-rule errors (PRD §4.1: business_name required, catalogue must
    be non-empty) are checked against the *resolved* fields, because
    ForgeSpec v2's recon/sales variants don't carry literal top-level
    `business_name`/`catalogue` keys (see PRD §3) — resolution walks
    `business.name` / `products.store.name` and `channels` /
    `products.categories[].variants` as the v2 equivalents.
    """
    try:
        spec = ForgeSpec.model_validate(raw)
    except PydanticValidationError as exc:
        details = [
            FieldError(field=".".join(str(p) for p in e["loc"]) or "(root)", message=e["msg"])
            for e in exc.errors()
        ]
        raise ForgeSpecValidationError(details) from exc

    details: list[FieldError] = []

    if not spec.resolved_business_name():
        details.append(FieldError(
            field="business_name",
            message="required: set business_name, business.name, or products.store.name",
        ))

    if spec.resolved_item_count() == 0:
        details.append(FieldError(
            field="catalogue",
            message="required and must be non-empty: set catalogue[], channels[], or products.categories[].variants[]",
        ))

    if details:
        raise ForgeSpecValidationError(details)

    return spec
