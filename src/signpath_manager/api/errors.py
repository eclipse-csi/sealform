"""API error types."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """An HTTP error answered by the SignPath API (RFC 9457 problem details when available)."""

    def __init__(
        self,
        method: str,
        path: str,
        status: int,
        title: str | None = None,
        detail: str | None = None,
        errors: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.method = method
        self.path = path
        self.status = status
        self.title = title
        self.detail = detail
        self.errors = errors or {}
        self.trace_id = trace_id
        super().__init__(str(self))

    @property
    def reason(self) -> str:
        parts: list[str] = []
        if self.detail:
            parts.append(self.detail)
        for field, messages in self.errors.items():
            if isinstance(messages, list):
                parts.extend(f"{field}: {m}" for m in messages)
            else:
                parts.append(f"{field}: {messages}")
        if not parts and self.title:
            parts.append(self.title)
        return "; ".join(parts) or "no details"

    def __str__(self) -> str:
        trace = f" (trace {self.trace_id})" if self.trace_id else ""
        return f"{self.method} {self.path} -> HTTP {self.status}: {self.reason}{trace}"

    @property
    def feature_disabled(self) -> bool:
        return self.status == 403 and "feature is disabled" in (self.detail or "")


class NotFoundError(ApiError):
    pass


class TransportError(Exception):
    """The API could not be reached."""


class StalePlanError(Exception):
    """A resource changed between plan and apply (emulated precondition failed)."""
