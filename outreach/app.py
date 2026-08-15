"""BLK Bridge Phase G dashboard and authenticated pipeline API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from contacts.hunter_guard import HunterBudgetError, HunterScopeError
from dashboard.auth import DashboardUser, current_user
from dashboard.models import (
    ContactPreviewRequest,
    ContactRunRequest,
    CrossOwnerDraftRequest,
    DraftRequest,
    IntakeRequest,
    ReviewRequest,
    TargetBatchRequest,
)
from dashboard.services import (
    DashboardServiceError,
    dashboard_state,
    derive_status_batch,
    generate_cross_owner_draft,
    generate_owner_draft,
    intake_targets,
    mark_draft_sent,
    preview_contact_run,
    research_batch,
    review_draft,
    run_contact_discovery,
)
from dashboard.storage import (
    SupabaseConfigurationError,
    SupabaseRequestError,
    SupabaseStorage,
    get_storage,
)
from drafts.generate import DraftGenerationError

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"

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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "blk-bridge"}


@app.get("/api/config")
def public_config(storage: SupabaseStorage = Depends(get_storage)) -> dict[str, str]:
    return {
        "supabase_url": storage.settings.url,
        "supabase_publishable_key": storage.settings.publishable_key,
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
    rows = intake_targets(storage, user, payload.firms)
    return {
        "targets": rows,
        "research_target_ids": [row["id"] for row in rows if row.get("domain")]
        if payload.run_research else [],
    }


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
    )


@app.post("/api/drafts/cross-owner")
def create_cross_owner_draft_endpoint(
    payload: CrossOwnerDraftRequest,
    user: DashboardUser = Depends(current_user),
    storage: SupabaseStorage = Depends(get_storage),
) -> dict[str, Any]:
    return generate_cross_owner_draft(
        storage,
        user,
        target_owner=payload.target_owner,
        target_slug=payload.target_slug,
        paragraph=payload.firm_specific_paragraph,
        confirmation_text=payload.confirmation_text,
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
