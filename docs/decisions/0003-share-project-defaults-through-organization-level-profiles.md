---
status: accepted
date: 2026-11-20
---

# Share project defaults through organization-level profiles

## Context and Problem Statement

Most projects will use the same signing policies (test and release), certificates, trusted build systems, origin verification rules, pipeline policies and default artifact configuration.
Writing all of this in every project file would repeat the same settings hundreds of times.
How should common settings be shared while allowing per-project differences?

## Decision Drivers

* Project files should stay around ten lines for the common case.
* Exceptions must be possible per project, down to a single field.
* Merging must be predictable: a reader must be able to tell the effective configuration.

## Considered Options

* Named profiles merged into projects
* Explicit configuration in every project file

## Decision Outcome

Chosen option: "Named profiles merged into projects", because it keeps project files minimal while allowing field-level overrides.

A profile is a file `profiles/<name>.yaml` with the same keys as a project except `name` and `ci_users`.
A project uses `default_profile` from `organization.yaml` unless it sets `profile: <name>`, or `profile: null` to use none.
Merge rules:

* maps are merged deeply by key;
* scalars and lists replace the inherited value, they are never concatenated;
* `null` removes an inherited map entry (for example `signing_policies: {test-signing: null}`);
* the source keys of an artifact configuration (`file`, `xml`; originally also `shared`, see [ADR-0014](0014-reference-artifact-configuration-xml-by-file-path.md)) replace each other as a group.

### Consequences

* Good, because changing a profile updates every project using it, and `plan` lists all of them.
* Good, because the merge rules are few and documented.
* Bad, because the effective configuration of a project is not visible in one file (mitigated by `plan` showing resolved values).

### Confirmation

Unit tests cover each merge rule; `validate` reports profile references to unknown profiles.

## Pros and Cons of the Options

### Named profiles merged into projects

* Good, because common settings are written once.
* Bad, because readers must know the merge rules.

### Explicit configuration in every project file

* Good, because each file shows the full effective configuration.
* Bad, because organization-wide changes require editing hundreds of files.
