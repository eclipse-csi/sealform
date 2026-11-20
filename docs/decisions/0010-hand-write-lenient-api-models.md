---
status: accepted
date: 2026-11-20
---

# Hand-write lenient API models

## Context and Problem Statement

The tool uses about 40 of the 120 SignPath API operations.
The plan proposed generating pydantic models from the OpenAPI document, but probing the sandbox showed responses that do not match it: the signing policy detail returns numeric `status` values (`8`, `64`) for invalid policies, where the document declares a string enum.
How should API payloads be modelled?

## Decision Drivers

* Responses that deviate from the OpenAPI document must not crash the tool.
* Only the fields the tool uses matter.
* Upstream API changes should be noticed.

## Considered Options

* Hand-written lenient models for the operations used, with the OpenAPI document vendored for drift detection
* Models generated from the OpenAPI document

## Decision Outcome

Chosen option: "Hand-written lenient models for the operations used, with the OpenAPI document vendored for drift detection", because generated strict enums reject real responses, and lenient models only need to cover what the tool reads.

Models ignore unknown fields and accept unknown enum values.
A scheduled workflow compares the vendored OpenAPI document and XSD with upstream.

### Consequences

* Good, because real-world deviations from the document do not break the tool.
* Bad, because models are maintained by hand and new fields are not picked up automatically.

### Confirmation

Client tests use payloads shaped like real sandbox responses, including numeric statuses.

## Pros and Cons of the Options

### Hand-written lenient models for the operations used, with the OpenAPI document vendored for drift detection

* Good, because the models describe what the API actually returns.
* Bad, because they need manual updates.

### Models generated from the OpenAPI document

* Good, because they stay in sync with the document.
* Bad, because the document is not accurate for all responses.
