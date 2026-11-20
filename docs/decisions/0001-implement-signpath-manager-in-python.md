---
status: accepted
date: 2026-11-20
---

# Implement signpath-manager in Python

## Context and Problem Statement

signpath-manager is a new CLI that reconciles YAML configuration with the SignPath REST API.
It will be maintained by the Eclipse Foundation security team and run both by operators and from the CI of configuration repositories.
Which language and runtime should it be written in?

## Decision Drivers

* The team already maintains otterdog, a Python configuration-as-code tool for GitHub organizations.
* The configuration model needs strong validation with precise error locations and an exportable JSON Schema for editor support.
* The tool must be easy to install in CI and on operator machines.

## Considered Options

* Python
* Go
* Java

## Decision Outcome

Chosen option: "Python", because it matches the team's existing stack (otterdog) and pydantic provides validation and JSON Schema generation for free.

### Consequences

* Good, because maintainers can reuse otterdog practices and libraries (pydantic, ruamel.yaml, rich, click).
* Good, because the configuration schema, validation messages and JSON Schema come from one set of model classes.
* Bad, because distribution needs a Python runtime (mitigated by publishing to PyPI and as a container image).

### Confirmation

`pyproject.toml` declares the package; CI runs ruff, mypy (strict) and pytest on every supported Python version.

## Pros and Cons of the Options

### Python

* Good, because it is the stack of otterdog.
* Good, because pydantic gives typed models, validation and JSON Schema.
* Bad, because it is not a single static binary.

### Go

* Good, because it produces a single static binary that is trivial to install in CI.
* Good, because a client could be generated from the OpenAPI document.
* Bad, because YAML validation with good error locations and JSON Schema export need more hand-written code.

### Java

* Good, because it is familiar to many Eclipse committers.
* Bad, because distribution is heavier (JVM or native-image) for a small CLI.
