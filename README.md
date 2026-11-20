# signpath-manager

Manage a [SignPath.io](https://signpath.io) organization as code.

Projects, signing policies, artifact configurations, CI users and pipeline policies are declared
in YAML files and reconciled against the SignPath REST API with a `plan` / `apply` workflow.

> Status: early development. See [PLAN.md](PLAN.md) for the design and
> [docs/decisions](docs/decisions/README.md) for the architectural decision records.

This repository contains the tool only.
Organization configurations (for example the Eclipse Foundation one) live in their own repositories.

## Development

```console
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
uv run mypy
```

## License

[Eclipse Public License 2.0](LICENSE)
