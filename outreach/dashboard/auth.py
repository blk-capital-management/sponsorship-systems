"""Authenticated two-user identity resolution for BLK Bridge."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status

from common.owners import normalize_owner
from dashboard.storage import (
    SupabaseConfigurationError,
    SupabaseRequestError,
    SupabaseStorage,
    get_storage,
)


@dataclass(frozen=True)
class DashboardUser:
    user_id: str
    email: str
    owner: str
    display_name: str
    gmail_sender: str
    access_token: str


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
) -> DashboardUser:
    try:
        auth_user = storage.auth_user(token)
        user_id = str(auth_user.get("id") or "")
        email = str(auth_user.get("email") or "").lower()
        profiles = storage.select(
            "profiles", token, params={"user_id": f"eq.{user_id}", "limit": "1"}
        )
    except (SupabaseRequestError, SupabaseConfigurationError) as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    if not user_id or len(profiles) != 1:
        raise HTTPException(
            status_code=403,
            detail="This Auth identity is not one of the two configured BLK Bridge users.",
        )
    profile = profiles[0]
    try:
        owner = normalize_owner(profile.get("owner"))
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if str(profile.get("email") or "").lower() != email:
        raise HTTPException(status_code=403, detail="Auth and profile email mismatch.")
    return DashboardUser(
        user_id=user_id,
        email=email,
        owner=owner,
        display_name=str(profile.get("display_name") or ""),
        gmail_sender=str(profile.get("gmail_sender") or ""),
        access_token=token,
    )
