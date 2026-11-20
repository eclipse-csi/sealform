---
status: accepted
date: 2026-11-20
---

# Treat deactivation as removal and prune only on request

## Context and Problem Statement

The SignPath API has no DELETE endpoint for any object; projects, signing policies, artifact configurations, CI users and pipeline policies can only be deactivated.
Deactivated slugs cannot be reused, and the default artifact configuration cannot be deactivated (confirmed against the sandbox).
What should the tool do with objects that exist in SignPath but not in the configuration?

## Decision Drivers

* Deactivating a project or CI user can break signing for a team.
* The organization already contains objects that are not yet in the configuration.
* Some CI users (automation, directory synchronization) are intentionally outside the configuration.

## Considered Options

* Report as drift; deactivate with `--prune`
* Always deactivate
* Ignore

## Decision Outcome

Chosen option: "Report as drift; deactivate with `--prune`", because it surfaces unmanaged objects without risking accidental deactivation.

Rules:

* objects listed in `unmanaged` are ignored entirely, the authenticated user is never deactivated;
* inactive objects that are not declared are ignored;
* a declared object whose slug matches an inactive one is reactivated instead of created;
* `--prune` refuses to run when more than `--max-deactivations` (default 10) objects would be deactivated;
* projects and CI users are pruned only on a full-organization run;
* inside a managed object the configuration is authoritative: undeclared submitters, approvers, readers and configurators are removed regardless of `--prune`.

### Consequences

* Good, because nothing is deactivated without an explicit flag.
* Bad, because a removed configuration entry leaves the object active until someone runs with `--prune`.

### Confirmation

Plan tests cover drift reporting, the `unmanaged` lists and the mass-deactivation guard.

## Pros and Cons of the Options

### Report as drift; deactivate with `--prune`

* Good, because it is safe by default and still converges on request.
* Bad, because it needs an extra flag.

### Always deactivate

* Good, because the configuration is the whole truth.
* Bad, because a mistake in the configuration immediately breaks signing for teams.

### Ignore

* Good, because it never surprises.
* Bad, because the organization drifts silently.
