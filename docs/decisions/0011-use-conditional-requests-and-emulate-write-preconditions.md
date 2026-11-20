---
status: accepted
date: 2026-11-20
---

# Use conditional requests and emulate write preconditions

## Context and Problem Statement

API requests should be conditional, to be a good citizen towards SignPath and to avoid overwriting concurrent changes.
Probing showed the SignPath API currently returns no `ETag`, `Last-Modified` or `Cache-Control` headers, answers `200` to `If-None-Match` and `If-Modified-Since`, and ignores `If-Match` on write operations.
How can the tool make conditional requests anyway?

## Decision Drivers

* Requests must be conditional wherever the API allows it, now or in the future.
* A plan is reviewed before it is applied; changes made in the web UI in the meantime must not be silently overwritten.
* The tool must not generate needless traffic.

## Considered Options

* Support HTTP validators transparently and emulate write preconditions by re-reading before each write
* Support HTTP validators only
* Plain requests

## Decision Outcome

Chosen option: "Support HTTP validators transparently and emulate write preconditions by re-reading before each write", because it is conditional today for writes and becomes conditional for reads as soon as SignPath emits validators.

* GET: the client remembers `ETag` and `Last-Modified` of each response and sends `If-None-Match` and `If-Modified-Since` on the next request for the same URL, serving the cached body on `304`.
* Writes: before each write the client re-reads the resource and compares a fingerprint with the state the plan was computed from; on mismatch the action fails as stale. When an `ETag` is known, it is also sent as `If-Match`.
* No write is sent when the plan shows no difference.
* Concurrency is bounded, `429` and `503` honor `Retry-After` with exponential backoff, connections are reused, and requests carry a `User-Agent` naming the tool.

### Consequences

* Good, because concurrent UI changes made between `plan` and `apply` are detected.
* Bad, because each write costs one extra GET while SignPath does not support `If-Match`.

### Confirmation

Client tests cover `304` handling, `If-Match` propagation and stale-plan detection.

## Pros and Cons of the Options

### Support HTTP validators transparently and emulate write preconditions by re-reading before each write

* Good, because it protects against lost updates today.
* Bad, because of the extra GET per write.

### Support HTTP validators only

* Good, because it costs nothing extra.
* Bad, because it has no effect until SignPath emits validators.

### Plain requests

* Good, because it is simplest.
* Bad, because concurrent changes can be overwritten.
