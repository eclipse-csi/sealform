---
status: accepted
date: 2026-11-20
---

# Declare one organization per configuration root with one file per project

## Context and Problem Statement

The Eclipse Foundation has about 400 projects, each of which maps to one SignPath project.
The configuration must stay readable at that size, and the SignPath organization ID should not be repeated everywhere.
How should the configuration files be laid out?

## Decision Drivers

* One Eclipse project equals one SignPath project; reviewers should find a project's configuration immediately.
* The organization ID must be declared once.
* The tool lives in its own repository; organization configurations live elsewhere and are never mixed with the tool.

## Considered Options

* One configuration root per organization, `organization.yaml` plus one `projects/<slug>.yaml` per project
* One large YAML file per organization
* Organization ID in every project file

## Decision Outcome

Chosen option: "One configuration root per organization, `organization.yaml` plus one `projects/<slug>.yaml` per project", because it keeps project changes isolated in reviews and declares the organization ID once.

The project slug is the file name without `.yaml`, so the two can never disagree.
Profiles live in `profiles/<name>.yaml`, shared artifact configuration XML in `artifact-configurations/`.
All file references are relative to the configuration root and may not escape it.

### Consequences

* Good, because a pull request touching one project touches one file.
* Good, because the organization ID cannot drift between files.
* Neutral, because renaming a project slug means renaming its file (SignPath allows slug changes, the tool sees a rename as a new project).
* Bad, because several organizations need several configuration roots.

### Confirmation

`sealform validate` rejects project files whose name is not a valid slug and slugs that collide case-insensitively.

## Pros and Cons of the Options

### One configuration root per organization, `organization.yaml` plus one `projects/<slug>.yaml` per project

* Good, because files stay small and reviewable.
* Bad, because cross-project views need tooling (`plan` provides one).

### One large YAML file per organization

* Good, because everything is in one place.
* Bad, because a 400-project file is hard to review and causes merge conflicts.

### Organization ID in every project file

* Good, because each file is self-contained.
* Bad, because the ID is repeated 400 times and can drift.
