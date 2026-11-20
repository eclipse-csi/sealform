from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from sealform.api.errors import ApiError, NotFoundError, TransportError
from sealform.api.http import USER_AGENT, HttpSession

ORG = "11111111-2222-3333-4444-555555555555"
TOKEN = SecretStr("s3cr3t-token-value")


def session(handler: httpx.MockTransport, sleeps: list[float] | None = None) -> HttpSession:
    return HttpSession(
        "https://api.example/Api",
        ORG,
        TOKEN,
        transport=handler,
        sleep=(sleeps.append if sleeps is not None else None) or (lambda _s: None),
    )


def test_headers_and_base_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    s = session(httpx.MockTransport(handler))
    assert s.get("/Projects").json() == {"ok": True}
    req = seen[0]
    assert str(req.url) == f"https://api.example/Api/v1/{ORG}/Projects"
    assert req.headers["Authorization"] == "Bearer s3cr3t-token-value"
    assert req.headers["User-Agent"] == USER_AGENT


def test_conditional_get_with_etag() -> None:
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.headers))
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json={"n": 1}, headers={"ETag": '"v1"'})

    s = session(httpx.MockTransport(handler))
    first = s.get("/Projects/a")
    second = s.get("/Projects/a")
    assert first.json() == second.json() == {"n": 1}
    assert not first.from_cache
    assert second.from_cache
    assert "if-none-match" not in calls[0]
    assert calls[1]["if-none-match"] == '"v1"'


def test_conditional_get_with_last_modified() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("If-Modified-Since"):
            return httpx.Response(304)
        return httpx.Response(200, json=[1], headers={"Last-Modified": "Wed, 01 Jan 2026 00:00:00 GMT"})

    s = session(httpx.MockTransport(handler))
    s.get("/x")
    assert s.get("/x").from_cache


def test_no_validators_means_no_cache() -> None:
    count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count
        count += 1
        assert "If-None-Match" not in request.headers
        return httpx.Response(200, json={"n": count})

    s = session(httpx.MockTransport(handler))
    assert s.get("/x").json() == {"n": 1}
    assert s.get("/x").json() == {"n": 2}


def test_write_sends_if_match_and_invalidates_cache() -> None:
    writes: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            writes.append(request)
            return httpx.Response(204)
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json={}, headers={"ETag": '"v1"'})

    s = session(httpx.MockTransport(handler))
    s.get("/Projects/a")
    s.post("/Projects/a/Edit", {"name": "x"})
    assert writes[0].headers["If-Match"] == '"v1"'
    assert s.known_etag("/Projects/a") is None  # invalidated by our own write


def test_retry_after_on_429() -> None:
    sleeps: list[float] = []
    responses = iter([httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200, json=1)])

    s = session(httpx.MockTransport(lambda r: next(responses)), sleeps)
    assert s.get("/x").json() == 1
    assert sleeps == [3.0]


def test_get_retries_503_but_post_does_not() -> None:
    sleeps: list[float] = []
    responses = iter([httpx.Response(503), httpx.Response(200, json=1)])
    s = session(httpx.MockTransport(lambda r: next(responses)), sleeps)
    assert s.get("/x").json() == 1
    assert len(sleeps) == 1

    s2 = session(httpx.MockTransport(lambda r: httpx.Response(503, json={"title": "down"})), sleeps)
    with pytest.raises(ApiError) as e:
        s2.post("/x")
    assert e.value.status == 503


def test_post_retries_429() -> None:
    responses = iter([httpx.Response(429), httpx.Response(204)])
    s = session(httpx.MockTransport(lambda r: next(responses)), [])
    assert s.post("/x").status_code == 204


def test_problem_details_are_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "title": "One or more validation errors occurred.",
                "status": 400,
                "errors": {"SubmitterIds": ["The field 'SubmitterIds' cannot be empty."]},
                "traceId": "00-abc",
            },
        )

    s = session(httpx.MockTransport(handler))
    with pytest.raises(ApiError) as e:
        s.post("/Projects/a/SigningPolicies", {})
    assert e.value.status == 400
    assert "SubmitterIds: The field 'SubmitterIds' cannot be empty." in str(e.value)
    assert "00-abc" in str(e.value)
    assert "s3cr3t" not in str(e.value)


def test_not_found() -> None:
    s = session(
        httpx.MockTransport(lambda r: httpx.Response(404, json={"detail": "No matching entity was found"}))
    )
    with pytest.raises(NotFoundError):
        s.get("/Projects/nope")


def test_feature_disabled_flag() -> None:
    body = {"status": 403, "detail": "The PipelinePolicies feature is disabled for this subscription."}
    s = session(httpx.MockTransport(lambda r: httpx.Response(403, json=body)))
    with pytest.raises(ApiError) as e:
        s.post("/PipelinePolicies", {})
    assert e.value.feature_disabled


def test_transport_errors_are_retried_for_get_then_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    sleeps: list[float] = []
    s = session(httpx.MockTransport(handler), sleeps)
    with pytest.raises(TransportError):
        s.get("/x")
    assert len(sleeps) == 4
