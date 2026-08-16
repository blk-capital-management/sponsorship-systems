"""Small Supabase REST/Auth client used by the dashboard server.

Normal requests carry the caller's access token so Postgres RLS remains the
authorization boundary. The secret key is used only by explicit provisioning
and CRM-snapshot helpers; it is never returned to the browser.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

import requests


class SupabaseConfigurationError(RuntimeError):
    """Raised when required runtime configuration is absent."""


class SupabaseRequestError(RuntimeError):
    """A failed Auth, REST, or RPC request with a safe diagnostic."""

    def __init__(self, operation: str, status_code: int, detail: str):
        super().__init__(f"Supabase {operation} failed ({status_code}): {detail[:500]}")
        self.operation = operation
        self.status_code = status_code
        self.detail = detail[:2000]


@dataclass(frozen=True)
class SupabaseSettings:
    url: str
    publishable_key: str
    secret_key: str | None = None

    @classmethod
    def from_environment(cls) -> "SupabaseSettings":
        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        publishable = (
            os.getenv("SUPABASE_PUBLISHABLE_KEY")
            or os.getenv("SUPABASE_ANON_KEY")
            or ""
        ).strip()
        secret = (
            os.getenv("SUPABASE_SECRET_KEY")
            or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
            or ""
        ).strip() or None
        if not url or not publishable:
            raise SupabaseConfigurationError(
                "SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY are required."
            )
        return cls(url=url, publishable_key=publishable, secret_key=secret)


class SupabaseStorage:
    """HTTP client with explicit caller-token and service-token lanes."""

    def __init__(self, settings: SupabaseSettings, *, timeout: int = 30):
        self.settings = settings
        self.timeout = timeout

    def _headers(
        self,
        *,
        token: str | None = None,
        service: bool = False,
        prefer: str | None = None,
    ) -> dict[str, str]:
        if service:
            if not self.settings.secret_key:
                raise SupabaseConfigurationError(
                    "A Supabase secret key is required for this provisioning operation."
                )
            key = self.settings.secret_key
        else:
            key = self.settings.publishable_key
        headers = {
            "apikey": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Opaque sb_secret keys belong only in the apikey header. Legacy
        # service_role JWTs still require Authorization. Caller-scoped requests
        # always pair the publishable key with the user's Auth JWT.
        if service and not key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {key}"
        elif not service and token:
            headers["Authorization"] = f"Bearer {token}"
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        operation: str,
        token: str | None = None,
        service: bool = False,
        params: Mapping[str, Any] | None = None,
        payload: Any = None,
        prefer: str | None = None,
    ) -> Any:
        response = requests.request(
            method,
            url,
            headers=self._headers(token=token, service=service, prefer=prefer),
            params=dict(params or {}),
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("message") or response.text
            except Exception:
                detail = response.text
            raise SupabaseRequestError(operation, response.status_code, detail)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def auth_user(self, token: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"{self.settings.url}/auth/v1/user",
            operation="auth user lookup",
            token=token,
        )

    # The caller-token and service-token lanes stay separate public methods on
    # purpose: service_* bypasses RLS, and that has to be visible at every call
    # site rather than hidden behind a keyword argument. Only the request
    # plumbing below is shared.

    def _do_select(
        self,
        table: str,
        *,
        token: str | None,
        service: bool,
        select: str,
        params: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        result = self._request(
            "GET",
            f"{self.settings.url}/rest/v1/{table}",
            operation=f"{'service ' if service else ''}select {table}",
            token=token,
            service=service,
            params={"select": select, **dict(params or {})},
        )
        return list(result or [])

    def _do_write(
        self,
        method: str,
        table: str,
        *,
        token: str | None,
        service: bool,
        operation: str,
        payload: Any = None,
        params: Mapping[str, Any] | None = None,
        return_rows: bool = True,
        resolution: str = "",
    ) -> list[dict[str, Any]]:
        prefer = f"{resolution}return=" + ("representation" if return_rows else "minimal")
        result = self._request(
            method,
            f"{self.settings.url}/rest/v1/{table}",
            operation=f"{'service ' if service else ''}{operation} {table}",
            token=token,
            service=service,
            params=params,
            payload=payload,
            prefer=prefer,
        )
        return list(result or []) if return_rows else []

    def select(
        self,
        table: str,
        token: str,
        *,
        select: str = "*",
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self._do_select(table, token=token, service=False,
                               select=select, params=params)

    def service_select(
        self,
        table: str,
        *,
        select: str = "*",
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self._do_select(table, token=None, service=True,
                               select=select, params=params)

    def insert(
        self,
        table: str,
        rows: dict[str, Any] | list[dict[str, Any]],
        token: str,
        *,
        return_rows: bool = True,
    ) -> list[dict[str, Any]]:
        return self._do_write("POST", table, token=token, service=False,
                              operation="insert", payload=rows,
                              return_rows=return_rows)

    def upsert(
        self,
        table: str,
        rows: dict[str, Any] | list[dict[str, Any]],
        token: str,
        *,
        on_conflict: str,
        return_rows: bool = True,
    ) -> list[dict[str, Any]]:
        return self._do_write("POST", table, token=token, service=False,
                              operation="upsert", payload=rows,
                              params={"on_conflict": on_conflict},
                              return_rows=return_rows,
                              resolution="resolution=merge-duplicates,")

    def service_upsert(
        self,
        table: str,
        rows: dict[str, Any] | list[dict[str, Any]],
        *,
        on_conflict: str,
        return_rows: bool = True,
    ) -> list[dict[str, Any]]:
        return self._do_write("POST", table, token=None, service=True,
                              operation="upsert", payload=rows,
                              params={"on_conflict": on_conflict},
                              return_rows=return_rows,
                              resolution="resolution=merge-duplicates,")

    def service_insert(
        self,
        table: str,
        rows: dict[str, Any] | list[dict[str, Any]],
        *,
        return_rows: bool = True,
    ) -> list[dict[str, Any]]:
        return self._do_write("POST", table, token=None, service=True,
                              operation="insert", payload=rows,
                              return_rows=return_rows)

    def update(
        self,
        table: str,
        values: dict[str, Any],
        token: str,
        *,
        params: Mapping[str, Any],
        return_rows: bool = True,
    ) -> list[dict[str, Any]]:
        return self._do_write("PATCH", table, token=token, service=False,
                              operation="update", payload=values,
                              params=params, return_rows=return_rows)

    def service_update(
        self,
        table: str,
        values: dict[str, Any],
        *,
        params: Mapping[str, Any],
        return_rows: bool = True,
    ) -> list[dict[str, Any]]:
        return self._do_write("PATCH", table, token=None, service=True,
                              operation="update", payload=values,
                              params=params, return_rows=return_rows)

    def delete(
        self,
        table: str,
        token: str,
        *,
        params: Mapping[str, Any],
    ) -> None:
        self._do_write("DELETE", table, token=token, service=False,
                       operation="delete", params=params, return_rows=False)

    def service_delete(self, table: str, *, params: Mapping[str, Any]) -> None:
        self._do_write("DELETE", table, token=None, service=True,
                       operation="delete", params=params, return_rows=False)

    def rpc(self, function: str, payload: dict[str, Any], token: str) -> Any:
        return self._request(
            "POST",
            f"{self.settings.url}/rest/v1/rpc/{function}",
            operation=f"rpc {function}",
            token=token,
            payload=payload,
        )


@lru_cache(maxsize=1)
def get_storage() -> SupabaseStorage:
    return SupabaseStorage(SupabaseSettings.from_environment())
