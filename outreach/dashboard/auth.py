"""Authenticated identity and lane resolution for BLK Bridge.

An account is either an "owner" (has its own lane) or a "viewer" (no owned
lane, access to both lanes only). Every account -- owner or viewer -- may
switch which lane it is currently acting in via the X-Blk-Lane header,
validated against public.profile_lane_access; DashboardUser.owner is that
resolved *active* lane, not a fixed per-account value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from fastapi import Depends, Header, HTTPException, status

from dashboard.storage import (
    SupabaseConfigurationError,
    SupabaseRequestError,
    SupabaseStorage,
    get_storage,
    reset_active_lane,
    set_active_lane,
)


@dataclass(frozen=True)
class DashboardUser:
    user_id: str
    email: str
    owner: str
    """The active lane this request is acting in (e.g. 'jamari' or 'fola')."""
    display_name: str
    """Display name of the active lane's owner (e.g. 'Jamari Myers')."""
    gmail_sender: str
    """Gmail send-as address of the active lane's owner."""
    access_token: str
    role: str
    """'owner' or 'viewer' -- the account's own role, not the active lane."""
    actor_display_name: str
    """The real signed-in person's own display name, regardless of lane."""
    available_lanes: tuple[str, ...]


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A Supabase bearer token is required.",
        )
    return token.strip()


def current_user(
    token: str = Depends(bearer_token),
    storage: SupabaseStorage = Depends(get_storage),
    x_blk_lane: str | None = Header(default=None, alias="X-Blk-Lane"),
) -> Iterator[DashboardUser]:
    try:
        auth_user = storage.auth_user(token)
        user_id = str(auth_user.get("id") or "")
        email = str(auth_user.get("email") or "").lower()
        profiles = storage.select(
            "profiles", token, params={"user_id": f"eq.{user_id}", "limit": "1"}
        )
        lane_access = storage.select(
            "profile_lane_access", token, params={"user_id": f"eq.{user_id}"}
        )
    except (SupabaseRequestError, SupabaseConfigurationError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if not user_id or len(profiles) != 1:
        raise HTTPException(
            status_code=403,
            detail="This Auth identity is not one of the configured BLK Bridge accounts.",
        )
    profile = profiles[0]
    if str(profile.get("email") or "").lower() != email:
        raise HTTPException(status_code=403, detail="Auth and profile email mismatch.")

    available = sorted({str(row["lane"]) for row in lane_access if row.get("lane")})
    if not available:
        raise HTTPException(
            status_code=403,
            detail="This account has no lane access configured.",
        )
    default_lane = next(
        (str(row["lane"]) for row in lane_access if row.get("is_default")),
        available[0],
    )
    requested = (x_blk_lane or "").strip().lower() or None
    if requested is None:
        active_lane = default_lane
    elif requested in available:
        active_lane = requested
    else:
        raise HTTPException(
            status_code=403,
            detail=f"Lane {requested!r} is not permitted for this account.",
        )

    reset_token = set_active_lane(active_lane)
    try:
        try:
            identity = storage.rpc("lane_identity", {"p_lane": active_lane}, token)
        except (SupabaseRequestError, SupabaseConfigurationError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if not identity:
            raise HTTPException(status_code=403, detail="Lane identity is not configured.")

        yield DashboardUser(
            user_id=user_id,
            email=email,
            owner=active_lane,
            display_name=str(identity.get("display_name") or ""),
            gmail_sender=str(identity.get("gmail_sender") or ""),
            access_token=token,
            role=str(profile.get("role") or "owner"),
            actor_display_name=str(profile.get("display_name") or ""),
            available_lanes=tuple(available),
        )
    finally:
        reset_active_lane(reset_token)
