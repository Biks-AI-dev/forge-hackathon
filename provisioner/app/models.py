from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict


class Persona(BaseModel):
    model_config = ConfigDict(extra="allow")
    agent_name: Optional[str] = None
    language: Optional[str] = None
    tone: Optional[str] = None
    owner_name: Optional[str] = None
    admin_name: Optional[str] = None


class Business(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    outlets: Optional[list[str]] = None
    bank: Optional[str] = None


class Channel(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    hits_bank: Optional[bool] = None
    fee_rate: Optional[float] = None
    settle_days: Optional[int] = None
    assumed: Optional[bool] = None


class ProductVariant(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    price: float
    aliases: Optional[list[str]] = None


class ProductCategory(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    variants: list[ProductVariant] = []


class Store(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    location: Optional[str] = None
    hours: Optional[str] = None
    wa_number: Optional[str] = None
    kurir: Optional[str] = None


class Products(BaseModel):
    model_config = ConfigDict(extra="allow")
    store: Store
    categories: list[ProductCategory] = []


class Policy(BaseModel):
    model_config = ConfigDict(extra="allow")
    currency: Optional[str] = None
    payment: Optional[str] = None
    guardrails: Optional[list[str]] = None


class ForgeSpec(BaseModel):
    """ForgeSpec v2 (PRD §3). `extra="allow"` at every level: the Provisioner
    validates and routes on the fields it needs, it does not own this schema
    and must not drop fields the sandbox agent (Dev B) or auto-PRD needs.
    """

    model_config = ConfigDict(extra="allow")

    workflow: Optional[Literal["recon", "sales"]] = None

    # How the forged agent presents itself: a chat assistant (default) or an
    # app-shell UI (dashboard + embedded assistant). Set by the Architect
    # only when the client explicitly asked for an app/dashboard.
    ui_mode: Optional[Literal["chat", "app"]] = None

    # Legacy/generic top-level fields (PRD §4.1's original validation rule,
    # pre-dating the workflow discriminator). Still honored if present.
    business_name: Optional[str] = None
    catalogue: Optional[list[Any]] = None

    persona: Optional[Persona] = None
    business: Optional[Business] = None
    channels: Optional[list[Channel]] = None
    products: Optional[Products] = None
    policy: Optional[Policy] = None

    def resolved_business_name(self) -> Optional[str]:
        if self.business_name:
            return self.business_name
        if self.business and self.business.name:
            return self.business.name
        if self.products and self.products.store and self.products.store.name:
            return self.products.store.name
        return None

    def resolved_item_count(self) -> int:
        if self.catalogue is not None:
            return len(self.catalogue)
        if self.channels is not None:
            return len(self.channels)
        if self.products and self.products.categories:
            return sum(len(c.variants) for c in self.products.categories)
        return 0


class FieldError(BaseModel):
    field: str
    message: str


class ValidationErrorResponse(BaseModel):
    error: Literal["validation_failed"] = "validation_failed"
    message: str
    details: list[FieldError]


class ForgeErrorResponse(BaseModel):
    error: str
    message: str


class ForgeResponse(BaseModel):
    chat_url: str
    sandbox_id: str
    slug: str
    elapsed_ms: int
    replaced_sandbox_id: Optional[str] = None
