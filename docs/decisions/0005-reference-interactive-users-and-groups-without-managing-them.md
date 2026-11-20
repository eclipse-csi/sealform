---
status: accepted
date: 2026-11-20
---

# Reference interactive users and groups without managing them

## Context and Problem Statement

Projects have readers and configurators, signing policies have submitters and approvers; all of these are interactive users or user groups identified by UUID.
The API can invite users and edit groups, but the agreed scope of the tool is projects and CI users.
Should the tool create and maintain users and groups?

## Decision Drivers

* Scope: projects and CI users are fully in scope, people are not.
* Enterprise organizations may synchronize users and groups from a directory (SCIM); synchronized objects cannot be edited.
* Inviting users consumes the active interactive user quota and sends emails.

## Considered Options

* Reference only
* Manage groups, reference users
* Manage users and groups

## Decision Outcome

Chosen option: "Reference only", because it matches the agreed scope and stays compatible with directory synchronization.

Configuration refers to a user by email (`alice@example.org`) and to a group by name (`group:Release managers`).
The tool resolves references to IDs against the live organization and fails the plan if one is missing or ambiguous.
A user email matches the account email, the email part of the login identity, then the notification email.

### Consequences

* Good, because the tool never sends invitations or changes roles.
* Bad, because a project referencing a new person needs that person to be invited first, outside the tool.
* Bad, because resolving emails needs one API call per interactive user (the list endpoint does not return emails).

### Confirmation

`plan` reports unresolved references as errors and never issues user or group write calls.

## Pros and Cons of the Options

### Reference only

* Good, because it is simple and safe.
* Bad, because onboarding a person is a two-step process.

### Manage groups, reference users

* Good, because per-project approver groups could be maintained as code.
* Bad, because it conflicts with directory-synchronized groups.

### Manage users and groups

* Good, because everything would be declared in one place.
* Bad, because it sends invitations, consumes quota and conflicts with directory synchronization.
