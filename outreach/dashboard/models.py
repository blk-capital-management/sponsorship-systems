"""Validated dashboard request shapes."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class IntakeFirm(BaseModel):
    firm: str = Field(min_length=2, max_length=160)
    domain: str = Field(default="", max_length=253)
    region: str = Field(default="US", max_length=40)
    firm_type: str = Field(default="", max_length=80)
    tier_target: str = Field(default="", max_length=40)
    priority: int = Field(default=3, ge=1, le=3)
    notes: str = Field(default="", max_length=1000)

    @field_validator("firm", "domain", "region", "firm_type", "tier_target", "notes")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("domain")
    @classmethod
    def clean_domain(cls, value: str) -> str:
        domain = value.lower().removeprefix("https://").removeprefix("http://")
        return domain.split("/", 1)[0].removeprefix("www.")


class IntakeRequest(BaseModel):
    firms: list[IntakeFirm] = Field(min_length=1, max_length=25)
    run_research: bool = True


class TargetBatchRequest(BaseModel):
    target_ids: list[str] = Field(min_length=1, max_length=25)


class ContactPreviewRequest(TargetBatchRequest):
    pass


class ContactRunRequest(BaseModel):
    run_id: str
    confirmed_credit_cap: int = Field(ge=0, le=10)


class DraftRequest(BaseModel):
    target_id: str
    contact_id: str | None = None
    firm_specific_paragraph: str | None = Field(default=None, max_length=3000)


class CrossOwnerDraftRequest(BaseModel):
    target_owner: Literal["jamari", "fola"]
    target_slug: str = Field(pattern=r"^[a-z0-9_]+$")
    firm_specific_paragraph: str | None = Field(default=None, max_length=3000)
    confirmation_text: str = Field(min_length=20, max_length=500)


class ReviewRequest(BaseModel):
    action: Literal["approved", "rejected"]
    reason: str | None = Field(default=None, max_length=1500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str | None) -> str | None:
        return " ".join(value.strip().split()) if value else None

    @model_validator(mode="after")
    def require_rejection_reason(self) -> "ReviewRequest":
        if self.action == "rejected" and not self.reason:
            raise ValueError("A rejection reason is required.")
        return self


class ResolveManualRequest(BaseModel):
    note: str = Field(min_length=1, max_length=1500)

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("A resolution note is required.")
        return cleaned
