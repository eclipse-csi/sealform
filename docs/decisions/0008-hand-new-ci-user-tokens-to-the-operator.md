---
status: accepted
date: 2026-11-20
---

# Hand new CI user tokens to the operator

## Context and Problem Statement

SignPath returns a CI user's API token only once, in the response to the creation or token regeneration call (`{"userId": ..., "apiToken": ...}`).
The tool creates CI users, so it receives tokens that must reach the project's build system.
Where should these tokens go?

## Decision Drivers

* A lost token can only be replaced by regenerating it, which breaks the previous one.
* Tokens must never appear in logs, plans or CI output.
* The first version should not depend on a secrets manager or GitHub credentials.

## Considered Options

* Hand to operator (terminal or private file), behind a sink interface
* Write to a secrets manager
* Set as a GitHub Actions secret

## Decision Outcome

Chosen option: "Hand to operator (terminal or private file), behind a sink interface", because it has no external dependency and a sink interface keeps the other options open.

Sinks in the first version:

* terminal: written to `/dev/tty` (never stdout), then the tool waits for acknowledgement;
* file: `--ci-token-file PATH` appends JSON lines to a file created with mode 0600;
* discard: `--discard-new-ci-tokens` must be passed explicitly; the token is recovered later with `ci-user rotate-token`.

A non-interactive run that would create a CI user without a sink fails before making any change.

### Consequences

* Good, because tokens never transit through stdout, logs or the plan.
* Bad, because unattended runs need a follow-up manual step to distribute new tokens.

### Confirmation

Tests assert that tokens never appear in rendered plans, logs or exceptions.

## Pros and Cons of the Options

### Hand to operator (terminal or private file), behind a sink interface

* Good, because it is simple and dependency-free.
* Bad, because it is manual.

### Write to a secrets manager

* Good, because tokens are stored safely without human handling.
* Bad, because it ties the tool to a specific secrets manager.

### Set as a GitHub Actions secret

* Good, because the token lands where the build needs it.
* Bad, because the tool would need GitHub credentials and knowledge of repository layouts.
