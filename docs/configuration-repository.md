# Running signpath-manager from a configuration repository

The organization configuration lives in its own repository.
A typical setup reviews changes in pull requests and applies them on merge.

## Tokens

| Token | Role | Used by |
| --- | --- | --- |
| Reader | CI user with the Global Reader role (not yet verified to read CI user details and user emails) | `plan` on pull requests (read-only) |
| Administrator | CI user with the Global Administrator role, or Project Administrator plus User Administrator | `apply` on the default branch |

Store them as repository or environment secrets and expose them as `SIGNPATH_API_TOKEN`.
Protect the environment that holds the administrator token (required reviewers, default branch only).

## New CI users in unattended runs

An unattended `apply` has no terminal to show the API token of a new CI user.
Without `--ci-token-file` or `--discard-new-ci-tokens`, it stops before changing anything when the plan creates CI users.
Two options:

* an operator runs `apply` for the project locally, receives the token, and stores it in the project's CI;
* the workflow passes `--discard-new-ci-tokens`, and an operator later runs
  `signpath-manager ci-user rotate-token <name>` to obtain a token.

## Example GitHub Actions workflow

```yaml
name: SignPath

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  plan:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2  # v10.3.3
      - run: uvx signpath-manager validate
      - run: uvx signpath-manager plan
        env:
          SIGNPATH_API_TOKEN: ${{ secrets.SIGNPATH_READER_TOKEN }}

  apply:
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    environment: signpath-production
    concurrency: signpath-apply
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
        with:
          persist-credentials: false
      - uses: astral-sh/setup-uv@68559e99cc807c1eb7e9238fd43a15bba460eec2  # v10.3.3
      - run: uvx signpath-manager apply --yes --discard-new-ci-tokens
        env:
          SIGNPATH_API_TOKEN: ${{ secrets.SIGNPATH_ADMIN_TOKEN }}
```

Pin `signpath-manager` to a version (`uvx signpath-manager@X.Y.Z`) once releases are published.
`--prune` is best kept to deliberate, manual runs.

## Bootstrapping from an existing organization

```console
signpath-manager --organization-id <uuid> import -o .
# write profiles/<name>.yaml from what most projects share, then:
signpath-manager import -o . --profile <name> --force
signpath-manager plan              # expect: No changes.
```

`import-report.md` lists what needs attention, for example CI users shared by several projects.
