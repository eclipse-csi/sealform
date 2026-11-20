"""HTTP session: authentication, conditional requests, retries (see ADR-0011)."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import SecretStr

from signpath_manager import __version__
from signpath_manager.api.errors import ApiError, NotFoundError, TransportError

log = logging.getLogger("signpath_manager.api")

USER_AGENT = f"signpath-manager/{__version__} (+https://github.com/eclipse-csi/signpath-manager)"
RETRYABLE_GET = {429, 502, 503, 504}
RETRYABLE_WRITE = {429}  # the request was not processed


@dataclass
class _Cached:
    etag: str | None
    last_modified: str | None
    status_code: int
    content: bytes
    content_type: str | None


@dataclass
class Response:
    status_code: int
    content: bytes
    headers: dict[str, str]
    from_cache: bool = False

    def json(self) -> Any:
        import json

        return json.loads(self.content) if self.content else None

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    @property
    def etag(self) -> str | None:
        return self.headers.get("etag")


class HttpSession:
    """Authenticated session bound to one organization.

    * GET requests are conditional: validators (ETag / Last-Modified) seen on previous
      responses are sent back as If-None-Match / If-Modified-Since and a 304 is served
      from the cache. SignPath does not emit validators today, so this is inert until it does.
    * Writes send If-Match when an ETag is known for the target resource.
    * 429/502/503/504 on GET and 429 on writes are retried with exponential backoff and
      Retry-After.
    """

    def __init__(
        self,
        api_url: str,
        organization_id: str,
        token: SecretStr,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
        max_retries: int = 4,
        sleep: Callable[[float], None] = time.sleep,
        max_connections: int = 16,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.organization_id = organization_id
        self.base_url = f"{self.api_url}/v1/{organization_id}"
        self._max_retries = max_retries
        self._sleep = sleep
        self._cache: dict[str, _Cached] = {}
        self._lock = threading.Lock()
        self.request_count = 0
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token.get_secret_value()}",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            limits=httpx.Limits(max_connections=max_connections, max_keepalive_connections=max_connections),
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpSession:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ helpers

    def url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return f"{self.base_url}{path}"

    def known_etag(self, path: str) -> str | None:
        with self._lock:
            cached = self._cache.get(self.url(path))
        return cached.etag if cached else None

    def _backoff(self, attempt: int, response: httpx.Response | None) -> float:
        if response is not None:
            retry_after = response.headers.get("retry-after")
            if retry_after:
                try:
                    return min(float(retry_after), 120.0)
                except ValueError:
                    try:
                        delta = parsedate_to_datetime(retry_after).timestamp() - time.time()
                        return max(0.0, min(delta, 120.0))
                    except (TypeError, ValueError):
                        pass
        return float(min(30.0, 0.5 * 2**attempt + random.uniform(0, 0.25)))  # noqa: S311

    def _raise_for(self, method: str, path: str, response: httpx.Response) -> None:
        title = detail = trace = None
        errors: dict[str, Any] | None = None
        try:
            body = response.json()
            if isinstance(body, dict):
                title = body.get("title")
                detail = body.get("detail")
                errors = body.get("errors") if isinstance(body.get("errors"), dict) else None
                trace = body.get("traceId")
        except ValueError:
            detail = response.text[:500] or None
        cls = NotFoundError if response.status_code == 404 else ApiError
        raise cls(method, path, response.status_code, title, detail, errors, trace)

    # ------------------------------------------------------------------ requests

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        accept: str | None = None,
        if_match: str | None = None,
    ) -> Response:
        url = self.url(path)
        is_get = method == "GET"
        headers: dict[str, str] = {}
        if accept:
            headers["Accept"] = accept
        cache_key = str(httpx.URL(url, params=params)) if is_get else url
        cached: _Cached | None = None
        if is_get:
            with self._lock:
                cached = self._cache.get(cache_key)
            if cached is not None:
                if cached.etag:
                    headers["If-None-Match"] = cached.etag
                if cached.last_modified:
                    headers["If-Modified-Since"] = cached.last_modified
        else:
            etag = if_match
            if etag is None:
                with self._lock:
                    target = self._cache.get(url.rsplit("/", 1)[0])
                etag = target.etag if target else None
            if etag:
                headers["If-Match"] = etag

        retryable = RETRYABLE_GET if is_get else RETRYABLE_WRITE
        attempt = 0
        while True:
            started = time.monotonic()
            try:
                response = self._client.request(method, url, json=json, params=params, headers=headers)
            except httpx.TransportError as e:
                if is_get and attempt < self._max_retries:
                    delay = self._backoff(attempt, None)
                    log.debug("%s %s failed (%s), retrying in %.1fs", method, path, e, delay)
                    self._sleep(delay)
                    attempt += 1
                    continue
                raise TransportError(f"{method} {path}: {e}") from e
            with self._lock:
                self.request_count += 1
            log.debug(
                "%s %s -> %s (%.0f ms)",
                method,
                path,
                response.status_code,
                (time.monotonic() - started) * 1000,
            )
            if response.status_code in retryable and attempt < self._max_retries:
                delay = self._backoff(attempt, response)
                log.info("%s %s -> %s, retrying in %.1fs", method, path, response.status_code, delay)
                self._sleep(delay)
                attempt += 1
                continue
            break

        if is_get and response.status_code == 304 and cached is not None:
            return Response(cached.status_code, cached.content, {"etag": cached.etag or ""}, from_cache=True)
        if response.status_code >= 400:
            self._raise_for(method, path, response)
        lowered = {k.lower(): v for k, v in response.headers.items()}
        if is_get:
            etag = lowered.get("etag")
            last_modified = lowered.get("last-modified")
            if etag or last_modified:
                with self._lock:
                    self._cache[cache_key] = _Cached(
                        etag,
                        last_modified,
                        response.status_code,
                        response.content,
                        lowered.get("content-type"),
                    )
        else:
            # our own write invalidates what we know about the resource
            with self._lock:
                for key in [k for k in self._cache if k.startswith(url.rsplit("/", 1)[0])]:
                    del self._cache[key]
        return Response(response.status_code, response.content, lowered)

    def get(self, path: str, **kwargs: Any) -> Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, json: Any = None, **kwargs: Any) -> Response:
        return self.request("POST", path, json=json, **kwargs)
