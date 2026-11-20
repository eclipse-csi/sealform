---
status: accepted
date: 2026-11-20
---

# Reuse signing policies through templates

## Context and Problem Statement

The same signing policies (for example release signing with the Eclipse Foundation release certificate, a trusted build system and origin verification) recur across many projects, with small variations such as the allowed branches.
Profiles ([ADR-0003](0003-share-project-defaults-through-organization-level-profiles.md)) already share policies, but a project has exactly one profile, so mixing policies from different sources means one profile per combination.
How should a signing policy be defined once and reused, with per-project overrides?

## Decision Drivers

* A policy must be reusable on its own, independently of the profile a project uses.
* Overrides must be field-level, e.g. replacing only `origin_verification.branches`.
* The effective policy must stay easy to trace back to its sources.
* References should work like the other file references (`file:` for XML, [ADR-0014](0014-reference-artifact-configuration-xml-by-file-path.md)).

## Considered Options

* Signing policy templates referenced by path, one level
* Signing policy templates referenced by name
* Templates that may extend other templates
* Profiles only

## Decision Outcome

Chosen option: "Signing policy templates referenced by path, one level", because it reuses policies independently of profiles, keeps references consistent with `file:`, and bounds every policy to at most three layers.

A template is a YAML file holding one signing policy (by convention in `signing-policies/`).
A signing policy entry in a profile or a project builds on it with `extends: <path>`, relative to the configuration root.
The effective policy is the template, overridden by the profile's entry, overridden by the project's entry, with the merge rules of ADR-0003 (maps merge deeply, lists replace, `null` removes).
The template is the one named by the project's `extends` when it has one, otherwise the profile's; `extends: null` drops the profile's template.
A template cannot use `extends`.

### Consequences

* Good, because a policy is written once and projects override single fields, e.g. `origin_verification: {branches: [main]}`.
* Good, because templates combine freely with profiles: a project can use its profile's policies and still switch one of them to another template.
* Neutral, because templates are validated once each, with errors located in the template file; a missing template is reported at the `extends` that names it.
* Bad, because a policy's effective value can now come from three files; `plan` shows resolved values, but reviewers must know the layering.
* Bad, because templates cannot build on each other, so near-identical templates repeat their common fields.

### Confirmation

Merge tests cover the layering, switching templates, `null` removals and `extends: null`; loader tests cover overrides, missing and nested templates and error locations; an import test checks that projects are written relative to a profile whose policies come from templates.

## Pros and Cons of the Options

### Signing policy templates referenced by path, one level

* Good, because it is explicit and consistent with `file:` references.
* Bad, because paths are longer than names.

### Signing policy templates referenced by name

* Good, because `extends: release` is short, like `profile: standard`.
* Bad, because it adds another name-to-folder convention, which ADR-0014 just removed for XML.

### Templates that may extend other templates

* Good, because variants (release with approval) could build on a base template.
* Bad, because chains make the origin of a value hard to see and need cycle detection.

### Profiles only

* Good, because nothing new is needed: profiles already merge field by field.
* Bad, because one profile per project forces a profile for every combination of policies.
