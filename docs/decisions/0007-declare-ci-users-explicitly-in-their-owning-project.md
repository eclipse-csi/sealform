---
status: accepted
date: 2026-11-20
---

# Declare CI users explicitly in their owning project

## Context and Problem Statement

CI users are organization-level objects identified by UUID and name (no slug), and they are the submitters of signing policies that verify trusted build systems.
SignPath recommends one CI user per project when origin verification is used.
Where and how should CI users be declared?

## Decision Drivers

* A CI user's API token is a credential of the project that uses it.
* Names must be unique among active users; deactivated users free their name (confirmed against the sandbox).
* Existing organizations may already share CI users between projects.

## Considered Options

* Per project, explicit
* Per project, implicit (one CI user named after the project)
* Organization-level list referenced by projects

## Decision Outcome

Chosen option: "Per project, explicit", because it keeps credentials next to the project that owns them without hidden conventions.

A project declares `ci_users: {<name>: {signing_policies: [...], ...}}`; each listed signing policy gets the CI user as submitter.
A CI user name may be declared by one project only.
A signing policy may also reference a CI user it does not own with `submitters: [ci:<name>]`, for CI users owned by another project or listed in `unmanaged.ci_users`; this produces a warning, so that shared CI users found by `import` can be represented and migrated.
CI users are matched by name, preferring the active user; an inactive user with the declared name is reactivated rather than duplicated.

### Consequences

* Good, because the owning project is always known, so pruning and token rotation are unambiguous.
* Good, because imports of organizations with shared CI users stay faithful.
* Bad, because a project-scoped apply cannot create a CI user owned by another project.

### Confirmation

`validate` rejects duplicate CI user names across projects and unknown signing policy names in `ci_users`.

## Pros and Cons of the Options

### Per project, explicit

* Good, because nothing is implicit.
* Bad, because every project file lists its CI user.

### Per project, implicit (one CI user named after the project)

* Good, because project files are shorter.
* Bad, because the naming convention is hidden and existing CI users with other names cannot be adopted.

### Organization-level list referenced by projects

* Good, because shared CI users are natural.
* Bad, because ownership of a credential becomes unclear.
