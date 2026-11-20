---
status: accepted
date: 2026-11-20
---

# Manage pipeline policies and reference trusted build systems

## Context and Problem Statement

Two organization-level objects are used by projects: trusted build systems (linked to projects, required for origin verification) and pipeline policies (YAML rules attached to signing policies at `Log` or `Enforce` level).
Should the tool manage these objects, or only reference them?

## Decision Drivers

* Pipeline policies are rules that change over time and benefit from review and history.
* Trusted build systems are set up once; custom ones return a secret token on creation.
* The API cannot unlink a trusted build system from a project.

## Considered Options

* Manage pipeline policies, reference trusted build systems
* Reference both
* Manage both

## Decision Outcome

Chosen option: "Manage pipeline policies, reference trusted build systems", because pipeline policies are policy-as-code that deserves versioning, while trusted build systems are a one-time, secret-bearing setup.

Pipeline policies are declared in `organization.yaml` with an inline `definition`, a `definition_file`, or an `external` reference to a `.signpath/policies/...` file in a repository.
Definitions are compared as parsed YAML.
Projects link trusted build systems by slug; a link present in SignPath but not declared is reported as drift that must be removed manually.

### Consequences

* Good, because policy changes go through review.
* Bad, because the sandbox subscription does not include pipeline policies, so this code path can only be tested against a fake API until a subscription with the feature is available.
* Bad, because extra trusted build system links cannot be fixed automatically.

### Confirmation

When no pipeline policy is declared, the tool never calls pipeline policy write endpoints; when one is declared on a subscription without the feature, the API error is reported clearly.

## Pros and Cons of the Options

### Manage pipeline policies, reference trusted build systems

* Good, because the changing part is versioned and the secret-bearing part stays manual.
* Bad, because trusted build systems must be created by hand before projects reference them.

### Reference both

* Good, because the tool's write surface is smaller.
* Bad, because pipeline policy changes are not reviewed.

### Manage both

* Good, because the organization is fully declared.
* Bad, because custom trusted build system tokens would need the same delivery mechanism as CI user tokens.
