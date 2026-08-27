"""Validated dashboard request shapes."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from common.config import normalize_firm_category
from contacts.patterns import render_address
from dashboard.crm import PIPELINE_STAGES, RELATIONSHIP_STATUSES


def _bare_domain(value: str) -> str:
    """Reduce a URL or host to a bare registrable domain."""
    domain = value.strip().lower().removeprefix("https://").removeprefix("http://")
    return domain.split("/", 1)[0].split("?", 1)[0].removeprefix("www.")


class IntakeFirm(BaseModel):
    firm: str = Field(min_length=2, max_length=160)
    domain: str = Field(default="", max_length=253)
    website: str = Field(default="", max_length=500)
    linkedin_url: str = Field(default="", max_length=500)
    region: str = Field(default="US", max_length=40)
    firm_type: str = Field(default="", max_length=80)
    tier_target: str = Field(default="", max_length=40)
    priority: int = Field(default=3, ge=1, le=3)
    notes: str = Field(default="", max_length=1000)
    email_format: str = Field(default="", max_length=120)
    email_format_source_url: str = Field(default="", max_length=500)

    # Set during validation, not supplied by the caller. False means the firm_type
    # was kept verbatim because it is outside the controlled vocabulary.
    firm_type_recognized: bool = Field(default=True, exclude=True)

    @field_validator("firm", "domain", "region", "firm_type", "tier_target", "notes",
                     "website", "linkedin_url", "email_format", "email_format_source_url")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("domain")
    @classmethod
    def clean_domain(cls, value: str) -> str:
        return _bare_domain(value)

    @field_validator("linkedin_url")
    @classmethod
    def clean_linkedin(cls, value: str) -> str:
        """Stored as a reference only.

        Rule 3 stands: nothing fetches this. common/http.py refuses linkedin.com
        at the request layer, so there is no scraping path either way.
        """
        if value and "linkedin.com" not in value.lower():
            raise ValueError("linkedin_url must be a linkedin.com URL.")
        return value

    @field_validator("firm_type")
    @classmethod
    def clean_firm_type(cls, value: str) -> str:
        canonical, _ = normalize_firm_category(value)
        return canonical

    @model_validator(mode="after")
    def resolve_intake(self) -> "IntakeFirm":
        # Sourcing usually turns up the website before the bare domain.
        if not self.domain and self.website:
            self.domain = _bare_domain(self.website)

        _, recognized = normalize_firm_category(self.firm_type)
        self.firm_type_recognized = recognized or not self.firm_type

        if self.email_format:
            # Rule 1 applied to patterns: unsourced is not weaker, it is not a pattern.
            if not self.email_format_source_url:
                raise ValueError(
                    "email_format_source_url is required whenever email_format is set. "
                    "Give the public page the format was read off."
                )
            # render_address is the existing parser and already rejects unknown
            # tokens. Round-tripping it here means one definition of a valid pattern.
            try:
                render_address(self.email_format, "Jane", "Doe",
                               self.domain or "example.com")
            except ValueError as exc:
                raise ValueError(
                    f"email_format is not a usable pattern: {exc}. "
                    "Use tokens like {first}, {last}, {f}, {l}, e.g. {f}{last}@acme.com."
                ) from exc
        elif self.email_format_source_url:
            raise ValueError(
                "email_format_source_url was given without an email_format."
            )
        return self


class IntakeRequest(BaseModel):
    firms: list[IntakeFirm] = Field(min_length=1, max_length=25)
    run_research: bool = True


class TargetUpdateRequest(BaseModel):
    """Attach a domain (directly, or read from a website URL) to an existing target.

    The common case this exists for: a firm was added by name only, so research
    could not start, and the operator now has a domain to give it.
    """

    domain: str | None = Field(default=None, max_length=253)
    website: str | None = Field(default=None, max_length=500)

    @field_validator("domain", "website")
    @classmethod
    def clean_text(cls, value: str | None) -> str | None:
        return " ".join(value.strip().split()) if value is not None else None

    @field_validator("domain")
    @classmethod
    def clean_domain(cls, value: str | None) -> str | None:
        return _bare_domain(value) if value else value

    @model_validator(mode="after")
    def resolve_update(self) -> "TargetUpdateRequest":
        if not self.domain and self.website:
            self.domain = _bare_domain(self.website)
        if not self.domain:
            raise ValueError("A domain, or a website to read one from, is required.")
        return self


class TargetBatchRequest(BaseModel):
    target_ids: list[str] = Field(min_length=1, max_length=25)


class StatusOverrideRequest(BaseModel):
    field: Literal["relationship_status", "pipeline_stage"]
    value: str | None = Field(default=None, max_length=80)
    clear: bool = False
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("value", "reason")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return " ".join(value.strip().split()) if value else None

    @model_validator(mode="after")
    def validate_status_change(self) -> "StatusOverrideRequest":
        if self.clear:
            if self.value:
                raise ValueError("A cleared override must not include a value.")
            return self
        if not self.value:
            raise ValueError("A status value is required.")
        allowed = (
            RELATIONSHIP_STATUSES
            if self.field == "relationship_status" else PIPELINE_STAGES
        )
        if self.value not in allowed:
            raise ValueError(f"Unrecognized {self.field.replace('_', ' ')}.")
        return self


class BatchStatusOverrideRequest(StatusOverrideRequest):
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
    supporting_hook_ids: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("supporting_hook_ids")
    @classmethod
    def clean_supporting_hook_ids(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("Each supporting research hook may be selected only once.")
        return cleaned


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


class ManualContactRequest(BaseModel):
    """A contact the operator found and verified themselves.

    Hunter is one way to find a recruiting contact, not the only way. This is
    the path for a name sourced by hand, e.g. seen on LinkedIn, referred by
    someone, or found on a page the crawler missed. source_note is free text
    for a reviewer, not a firm_claim_source: contact_provenance carries no URL
    rules (see common/namespaces.py), so a LinkedIn profile URL is fine here.
    """

    name: str = Field(min_length=1, max_length=160)
    title: str = Field(default="", max_length=160)
    email: str = Field(default="", max_length=254)
    source_note: str = Field(default="", max_length=500)

    @field_validator("title", "email", "source_note")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("A name is required.")
        return cleaned

    @field_validator("email")
    @classmethod
    def clean_email(cls, value: str) -> str:
        if value and "@" not in value:
            raise ValueError("email must contain @.")
        return value.lower()


class ResolveManualRequest(BaseModel):
    note: str = Field(min_length=1, max_length=1500)

    @field_validator("note")
    @classmethod
    def clean_note(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("A resolution note is required.")
        return cleaned


class MeetingNoteRequest(BaseModel):
    interaction_date: date
    interaction_type: str = Field(default="Meeting", max_length=80)
    participants: list[str] = Field(default_factory=list, max_length=50)
    notes: str = Field(min_length=1, max_length=12000)
    next_step: str = Field(default="", max_length=2000)
    follow_up_date: date | None = None

    @field_validator("interaction_type", "notes", "next_step")
    @classmethod
    def clean_note_text(cls, value: str) -> str:
        return " ".join(value.strip().split())

    @field_validator("participants")
    @classmethod
    def clean_participants(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            participant = " ".join(value.strip().split())
            if participant and participant not in cleaned:
                cleaned.append(participant)
        return cleaned

    @model_validator(mode="after")
    def validate_dates(self) -> "MeetingNoteRequest":
        if not self.notes:
            raise ValueError("Meeting notes are required.")
        return self
