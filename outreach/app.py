"""BLK Bridge Phase G dashboard and authenticated pipeline API."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from contacts.hunter_guard import HunterBudgetError, HunterScopeError
from common.config import load_firm_categories
from common.logging import get_logger
from dashboard.auth import DashboardUser, current_user
from dashboard.crm import PIPELINE_STAGES, RELATIONSHIP_STATUSES
from dashboard.models import (
    BatchStatusOverrideRequest,
    ContactPreviewRequest,
    ContactRunRequest,
    DraftRequest,
    IntakeRequest,
    ManualContactRequest,
    MeetingNoteRequest,
    ResolveManualRequest,
    ReviewRequest,
    StatusOverrideRequest,
    TargetBatchRequest,
    TargetUpdateRequest,
)
from dashboard.services import (
    DashboardServiceError,
    add_manual_contact,
    batch_status_override,
    create_meeting_note,
    dashboard_state,
    delete_target,
    derive_status_batch,
    generate_owner_draft,
    get_firm_detail,
    intake_targets,
    mark_draft_sent,
    preview_contact_run,
    remove_contact,
    resolve_manual_item,
    research_batch,
    review_draft,
    run_contact_discovery,
    update_meeting_note,
    update_status_override,
    update_target_domain,
)
from dashboard.storage import (
    SupabaseConfigurationError,
    SupabaseRequestError,
    SupabaseStorage,
    get_storage,
)
from drafts.generate import DraftGenerationError, load_template

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"

log = get_logger("app")

app = FastAPI(
    title="BLK Bridge",
    version="2026.08.phase-g",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def private_no_store(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "connect-src 'self' https://*.supabase.co; "
        "img-src 'self' data:; "
        "style-src 'self'; script-src 'self'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=()"
    )
    return response


@app.exception_handler(DashboardServiceError)
async def dashboard_error(_request: Request, exc: DashboardServiceError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(DraftGenerationError)
async def draft_error(_request: Request, exc: DraftGenerationError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(HunterBudgetError)
async def hunter_budget_error(_request: Request, exc: HunterBudgetError):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(HunterScopeError)
async def hunter_scope_error(_request: Request, exc: HunterScopeError):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(SupabaseRequestError)
async def supabase_error(_request: Request, exc: SupabaseRequestError):
    status = 403 if exc.status_code in (401, 403) else 502
    return JSONResponse(status_code=status, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    # Every expected failure mode above already has its own handler and a
    # curated, safe message. Anything reaching here is a bug in an arbitrary,
    # uncurated exception -- unlike the handlers above, its message could
    # contain internals (payload contents, driver errors, file paths), so it
    # is logged in full server-side (shows up in Vercel's function logs) but
    # never echoed to the client. The request id lets a report be matched
    # back to that log line without exposing anything itself.
    request_id = uuid.uuid4().hex[:12]
    log.exception(
        "Unhandled error [%s] on %s %s", request_id, request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={"detail": f"Something went wrong (ref {request_id}). "
                            "Check the server logs for that reference."},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "blk-bridge"}


@app.get("/api/config")
def public_config(storage: SupabaseStorage = Depends(get_storage)) -> dict[str, Any]:
    return {
        "supabase_url": storage.settings.url,
        "supabase_publishable_key": storage.settings.publishable_key,
        # The intake category list is served from config so the dropdown and the
        # validator can never disagree about what a valid category is.
        "firm_categories": [entry["label"] for entry in load_firm_categories()],
        "relationship_statuses": list(RELATIONSHIP_STATUSES),
        "pipeline_stages": list(PIPELINE_STAGES),
        # Served raw so the draft dialog can preview the real locked template
        # around the paragraph the operator is writing, with no second copy of
        # the wording to drift out of sync.
        "cold_prospect_template": load_template(),
    }


@app.get("/api/state")
def state(
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return dashboard_state(storage, user)


@app.post("/api/intake")
def intake(
    payload: IntakeRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    result = intake_targets(storage, user, payload.firms)
    rows = result["accepted"]
    return {
        "targets": rows,
        "skipped": result["skipped"],
        "warnings": result["warnings"],
        "research_target_ids": [row["id"] for row in rows if row.get("domain")]
        if payload.run_research else [],
    }


@app.patch("/api/targets/{target_id}")
def update_target_endpoint(
    target_id: str,
    payload: TargetUpdateRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return update_target_domain(storage, user, target_id, payload.domain)


@app.delete("/api/targets/{target_id}")
def delete_target_endpoint(
    target_id: str,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return {"target": delete_target(storage, user, target_id)}


@app.patch("/api/targets/{target_id}/status")
def update_status_endpoint(
    target_id: str,
    payload: StatusOverrideRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return update_status_override(
        storage,
        user,
        target_id,
        field=payload.field,
        value=payload.value,
        clear=payload.clear,
        reason=payload.reason,
    )


@app.post("/api/status-overrides/batch")
def batch_status_endpoint(
    payload: BatchStatusOverrideRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    results = batch_status_override(
        storage,
        user,
        payload.target_ids,
        field=payload.field,
        value=payload.value,
        clear=payload.clear,
        reason=payload.reason,
    )
    return {
        "results": results,
        "errors": [row for row in results if row.get("error")],
    }


@app.get("/api/firms/{target_id}")
def firm_detail_endpoint(
    target_id: str,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return get_firm_detail(storage, user, target_id)


@app.post("/api/firms/{target_id}/meeting-notes")
def create_meeting_note_endpoint(
    target_id: str,
    payload: MeetingNoteRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return create_meeting_note(
        storage,
        user,
        target_id,
        interaction_date=payload.interaction_date,
        interaction_type=payload.interaction_type,
        participants=payload.participants,
        notes=payload.notes,
        next_step=payload.next_step,
        follow_up_date=payload.follow_up_date,
    )


@app.patch("/api/meeting-notes/{note_id}")
def update_meeting_note_endpoint(
    note_id: str,
    payload: MeetingNoteRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return update_meeting_note(
        storage,
        user,
        note_id,
        interaction_date=payload.interaction_date,
        interaction_type=payload.interaction_type,
        participants=payload.participants,
        notes=payload.notes,
        next_step=payload.next_step,
        follow_up_date=payload.follow_up_date,
    )


@app.post("/api/research")
def research(
    payload: TargetBatchRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    results = research_batch(storage, user, payload.target_ids)
    return {
        "results": results,
        "errors": [row for row in results if row.get("error")],
    }


@app.post("/api/derive-status")
def derive_status_endpoint(
    payload: TargetBatchRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    results = derive_status_batch(storage, user, payload.target_ids)
    return {
        "targets": [row for row in results if not row.get("error")],
        "errors": [row for row in results if row.get("error")],
    }


@app.post("/api/contacts/preview")
def contacts_preview(
    payload: ContactPreviewRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return preview_contact_run(storage, user, payload.target_ids)


@app.post("/api/contacts/run")
def contacts_run(
    payload: ContactRunRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return run_contact_discovery(
        storage, user, payload.run_id, payload.confirmed_credit_cap
    )


@app.post("/api/targets/{target_id}/contacts")
def add_manual_contact_endpoint(
    target_id: str,
    payload: ManualContactRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return add_manual_contact(
        storage, user, target_id,
        name=payload.name, title=payload.title, email=payload.email,
        source_note=payload.source_note,
    )


@app.delete("/api/contacts/{contact_id}", status_code=204)
def delete_contact_endpoint(
    contact_id: str,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> None:
    remove_contact(storage, user, contact_id)


@app.post("/api/drafts")
def create_draft_endpoint(
    payload: DraftRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return generate_owner_draft(
        storage,
        user,
        target_id=payload.target_id,
        contact_id=payload.contact_id,
        paragraph=payload.firm_specific_paragraph,
        supporting_hook_ids=payload.supporting_hook_ids,
    )


@app.post("/api/drafts/{draft_id}/review")
def review_draft_endpoint(
    draft_id: str,
    payload: ReviewRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return review_draft(storage, user, draft_id, payload.action, payload.reason)


@app.post("/api/drafts/{draft_id}/sent")
def mark_draft_sent_endpoint(
    draft_id: str,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    """Human reports that they sent an approved draft. Bridge sends nothing."""
    return mark_draft_sent(storage, user, draft_id)


@app.post("/api/manual-queue/{item_id}/resolve")
def resolve_manual_endpoint(
    item_id: str,
    payload: ResolveManualRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return resolve_manual_item(storage, user, item_id, payload.note)


@app.get("/app.js", include_in_schema=False)
def javascript() -> FileResponse:
    return FileResponse(PUBLIC / "app.js", media_type="application/javascript")


@app.get("/styles.css", include_in_schema=False)
def stylesheet() -> FileResponse:
    return FileResponse(PUBLIC / "styles.css", media_type="text/css")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(PUBLIC / "index.html", media_type="text/html")


@app.exception_handler(SupabaseConfigurationError)
async def configuration_error(_request: Request, exc: SupabaseConfigurationError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})
