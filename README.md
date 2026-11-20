# sealform

Manage a [SignPath.io](https://signpath.io) organization as code.

Projects, signing policies, artifact configurations, CI users and pipeline policies are declared in YAML
and reconciled against the SignPath REST API with a `plan` / `apply` workflow.
Certificates, interactive users, user groups and trusted build systems are referenced, never modified.

This repository contains the tool only.
Organization configurations (for example the Eclipse Foundation one) live in their own repositories.

sealform is an independent project: it is not affiliated with, endorsed by, or maintained by the makers of SignPath.

## Install

sealform is published on PyPI: <https://pypi.org/project/sealform/>.

```console
uv tool run sealform --help     # or uvx; uv fetches a suitable Python if needed
uv tool install sealform       # or pipx install sealform
```

## Quick start

```console
# 1. bootstrap a configuration from an existing organization
export SIGNPATH_API_TOKEN=...            # never passed as a command-line argument
sealform --organization-id <uuid> import -o my-org
sealform -c my-org plan          # expect: "No changes."

# 2. edit the YAML, then review and apply
sealform -c my-org validate      # offline
sealform -c my-org plan
sealform -c my-org apply
```

A minimal project file, inheriting everything else from a profile:

```yaml
# projects/technology.cbi.yaml  (the file name is the project slug)
name: Eclipse CBI
repositories: [https://github.com/eclipse-cbi/cbi]
ci_users:
  technology.cbi:
    signing_policies: [test-signing, release-signing]
signing_policies:
  release-signing:
    approval:
      approvers: [alice@example.org, group:CBI leads]
```

See [docs/configuration.md](docs/configuration.md) for the full reference and [examples/basic](examples/basic)
for a complete configuration root.

## Commands

| Command | What it does |
| --- | --- |
| `validate` | Checks the configuration offline: schema, profile merge, references, XML against the SignPath XSD. |
| `plan` | Shows what `apply` would change. Read-only: it is designed for a Global Reader token (not yet verified). `-o json` and `--detailed-exitcode` are available for CI. |
| `apply` | Applies the plan after confirmation (`--yes` when not interactive). |
| `import -o DIR` | Writes the configuration of a live organization. `--profile NAME` writes projects relative to a profile. |
| `ci-user rotate-token NAME` | Regenerates a CI user's API token. |
| `schema [KIND]` | Prints the JSON Schema of `organization`, `profile` or `project` files, for editor completion. |

Scoping options for `plan` and `apply`:

| Option | Effect |
| --- | --- |
| `-p/--project SLUG` | Only these projects (repeatable). |
| `--org-only` | Only organization-level objects (pipeline policies, pruning). |
| `--prune` | Deactivate what is not declared, refused above `--max-deactivations` (default 10). |

### API token

The administrator token is read from `--token-file PATH`, `--token-stdin` or `$SIGNPATH_API_TOKEN`, in that order.
There is deliberately no option to pass it on the command line ([ADR-0012](docs/decisions/0012-read-the-api-token-from-environment-file-or-stdin-only.md)).

### New CI user tokens

SignPath returns a CI user's API token only once, when the user is created.
`apply` sends it to one destination, and it never appears on stdout or in logs:

* the terminal, by default when interactive;
* a file created with mode 0600 (`--ci-token-file PATH`);
* nowhere, with `--discard-new-ci-tokens`; run `ci-user rotate-token` later to get a usable token.

A non-interactive run that would create CI users without a destination stops before changing anything.

## How it behaves

* **Deactivation instead of deletion.** The SignPath API cannot delete objects.
  Undeclared objects are reported as drift and deactivated only with `--prune`.
  Declaring an object whose slug matches an inactive one reactivates it.
* **Changes made in the web UI are not overwritten.** Before its first write, `apply` re-reads every resource it will modify.
  If one changed since the plan was computed, that project is skipped as stale and the others are applied
  ([ADR-0011](docs/decisions/0011-use-conditional-requests-and-emulate-write-preconditions.md)).
* **Requests are conditional and polite.** HTTP validators are used when SignPath provides them, concurrency is bounded,
  `Retry-After` is honored, and nothing is written when nothing differs.
* **Artifact configuration XML is compared as text**, the way SignPath stores it, so the repository and the UI show the same document.

Design and decisions: [PLAN.md](PLAN.md) and [docs/decisions](docs/decisions/README.md).
Running the tool from a configuration repository: [docs/configuration-repository.md](docs/configuration-repository.md).

## Development

```console
uv sync
uv run pytest            # unit and fake-API tests; never use a real token in tests
uv run ruff check && uv run ruff format --check
uv run mypy
scripts/update-spec.sh   # refresh the vendored OpenAPI document and XSD
```

## Releasing

The version comes from git tags ([uv-dynamic-versioning](https://github.com/ninoseki/uv-dynamic-versioning)):
a commit tagged `vX.Y.Z` builds as `X.Y.Z`, any other commit as the next minor `.devN` pre-release.
To release, tag the commit on `main` and push the tag; the release workflow builds, checks that the
built version matches the tag, and publishes to [PyPI](https://pypi.org/project/sealform/).

```console
git tag -s vX.Y.Z -m "vX.Y.Z"
git push origin vX.Y.Z
```

## License

[Eclipse Public License 2.0](LICENSE)
