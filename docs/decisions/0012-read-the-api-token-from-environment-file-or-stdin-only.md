---
status: accepted
date: 2026-11-20
---

# Read the API token from environment, file or stdin only

## Context and Problem Statement

The tool authenticates with an administrator API token.
Command-line arguments are visible to other users through the process list and are kept in shell history.
How should the token be provided?

## Decision Drivers

* The token grants administrative access to the organization.
* The tool runs both interactively and in CI.

## Considered Options

* Environment variable, file or stdin; no command-line option
* Command-line option

## Decision Outcome

Chosen option: "Environment variable, file or stdin; no command-line option", because none of these channels expose the token in process listings or shell history.

Precedence: `--token-file PATH`, then `--token-stdin`, then `SIGNPATH_API_TOKEN`.
A token file readable by group or others produces a warning.
The token is held as a secret value that is never printed, and the `Authorization` header is redacted from debug logs.

### Consequences

* Good, because the token never appears in argument lists.
* Neutral, because CI systems pass it as a secret environment variable, which they already support.
* Bad, because a token in an environment variable is inherited by child processes; `--token-file` or `--token-stdin` avoid that when it matters.

### Confirmation

The CLI has no `--token` option; tests assert the token never appears in output.

## Pros and Cons of the Options

### Environment variable, file or stdin; no command-line option

* Good, because it is the safe default for both CI and terminals.
* Bad, because it is slightly less convenient for ad-hoc use.

### Command-line option

* Good, because it is convenient.
* Bad, because the token leaks to process listings and shell history.
