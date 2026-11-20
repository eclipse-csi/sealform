# signpath-manager — implementation plan

A CLI that manages a SignPath.io organization as code: projects, signing policies,
artifact configurations, CI users and pipeline policies are declared in YAML and
reconciled against the SignPath REST API.

* **This repo** holds the tool only.
* **The Eclipse Foundation configuration** (org ID, profiles, project files, XML
  library) lives in a separate repo. The two are never mixed. Examples in this
  repo are generic.

## 0. Status (2026-11-20)

Milestones M0 to M5 are implemented. Every command was exercised against the
sandbox organization: `plan`, `apply` (create and update), `import` with and
without a profile followed by a `plan` showing no change, and
`ci-user rotate-token` (through the test suite). User documentation is in the
[README](README.md), [docs/configuration.md](docs/configuration.md) and
[docs/configuration-repository.md](docs/configuration-repository.md).

Changes made to the plan during implementation:

* **API models are hand-written and lenient** rather than generated, because real
  responses deviate from the spec
  ([ADR-0010](docs/decisions/0010-hand-write-lenient-api-models.md)).
* **XML is compared as text,** not canonicalized, because SignPath stores it
  verbatim. The one transformation SignPath applies (prepending an XML
  declaration when there is none) is mirrored.
* **Preconditions are verified once, right before the first write,** rather than
  before each write. Otherwise the tool's own earlier writes would trip them.
  A stale project is skipped while the others are applied.
* **`ci:<name>` submitter references must be resolvable offline:** the CI user is
  declared by another project or listed in `unmanaged.ci_users`. This is so
  `--prune` can never deactivate a CI user that a policy still uses.
* **Pipeline policies require trusted build system verification,** because the API
  nests them inside that setting.
* **`null` removes an inherited value at any depth;** the pre-merge structural
  check ignores nulls.

Not yet done:

* End-to-end tests against a dedicated sandbox in CI. They need their own CI
  secret; the developer's sandbox token must never be used in tests.
* A first release (PyPI trusted publishing and the GHCR image must be set up).
* Confirming that a Global Reader token is enough for `plan`.

## 1. Decisions made

Each structural decision has an ADR in [docs/decisions](docs/decisions/README.md).

| Topic | Decision |
| --- | --- |
| Language | Python (same stack as otterdog) |
| Config format | YAML. One config root = one SignPath organization. One file per project. |
| Organization ID | Declared once in `organization.yaml`. Never in project files. |
| Policy defaults | Org-level **profiles**. A project inherits the default profile and overrides fields. |
| Artifact config XML | Shared library of XML files referenced by name. Per-project custom XML (inline or file) is allowed. |
| Interactive users and groups | **Reference only.** Must already exist. Resolved to IDs by email or group name. Never created. |
| Pipeline policies | **Managed** (org-level, in `organization.yaml`). |
| Trusted build systems | **Reference only.** Linked to projects by slug. Never created or edited. |
| Certificates | Out of scope. Referenced by slug from signing policies, validated read-only. |
| CI users | **Per project, explicit.** Each project file declares its CI users and which of its signing policies each one submits to. A CI user belongs to exactly one project. |
| New CI user tokens | **Handed to the operator** (TTY or 0600 file) in v1. Behind a sink interface so Vault or GitHub secrets can be added later. |
| Removal | No DELETE exists in the API. `plan` reports unmanaged objects as drift. `apply --prune` deactivates them. |
| Existing org | Many objects already exist, so **`import` is in v1**. |
| Admin token input | Env var, file, or stdin. Never a CLI argument. |

## 2. API facts that shape the design

From `https://app.signpath.io/Api/swagger/v1/swagger.json` (OpenAPI 3.0.4) and docs.signpath.io:

1. **No DELETE endpoints at all.** Every object only has `Activate` and `Deactivate`
   (both idempotent). Removing something means deactivating it, and a slug probably
   stays reserved (to verify).
2. **Trusted build systems can't be unlinked from a project.** The only call is
   `POST Projects/{slug}/TrustedBuildSystemLinks`. Extra links are reported as drift
   that has to be fixed by hand.
3. **CI users and groups have no slug.** They have a UUID and a name, so the tool
   matches them by name and fails on ambiguous duplicates. Projects, signing policies,
   artifact configs, pipeline policies, trusted build systems and certificates all
   have slugs (`[A-Za-z0-9.\-_]`, max 100, case-insensitive per the docs).
4. **A CI user's token is returned only once,** from `POST CIUsers` (body
   `{"userId", "apiToken"}`, verified) or `POST CIUsers/{id}/RegenerateApiToken`. A
   CI user's global role can't be set through the API, so CI users stay Regular
   Users, which is what submitters need.
5. **Creating a project requires `artifactConfigurationXml`.** SignPath creates an
   initial artifact config with slug `initial` and name "Initial version"
   (verified), which becomes the default. The tool renames it afterwards.
6. **Edits replace the whole object.** `SigningPolicies/{slug}/Edit` takes the complete
   `submitterIds`, `approval`, `trustedBuildSystemVerification` and `certificateSlug`.
   Within a managed object, the YAML is authoritative: any submitter or approver not
   declared is removed, whether or not `--prune` is passed.
7. **Reading full state takes many calls.** `GET Projects/{slug}` returns policies,
   artifact configs, trusted build system links, readers and configurators. It does
   not include a policy's allowed branches, build definitions or pipeline-policy
   levels, nor any artifact config XML. Those need one `GET` per policy and one
   `GET .../Xml` per artifact config, so about 2,000 GETs for ~400 projects. Fetching
   must be concurrent and bounded.
8. **Enterprise plan:** trusted build system verification and origin verification are
   optional for each signing policy (only the Open Source Code Signing plan makes
   them mandatory), so profiles decide. With trusted build system verification on,
   interactive users can't be submitters: such a policy only has CI users as
   submitters. Without it, people and groups can submit too. Features depend on the
   plan, so the tool never hard-codes them: it reads `subscriptionType` and quotas
   from `GET Organization` and lets API validation errors through with context.
   Users and groups may be directory-synchronized (Entra ID SCIM). That fits
   "reference only", since synchronized objects can't be edited anyway.
9. **Not in the API, so out of reach:** project webhooks, per-user notification
   settings, CI user global role.
10. **Least privilege:** `plan` only reads, so it can run with a **Global Reader**
    token (for example in pull requests). `apply` needs Global Administrator, or
    Project Administrator plus User Administrator.

## 3. Configuration repository layout (consumer side)

```text
<config-root>/
├── organization.yaml                 # org ID, default profile, pipeline policies, unmanaged lists
├── profiles/
│   └── standard.yaml                 # one file per profile
├── artifact-configurations/          # shared XML library; file name = reference name
│   ├── jar-signing.xml
│   ├── windows-exe.xml
│   └── custom/                       # convention for project-specific XML files
│       └── technology.foo-installer.xml
├── pipeline-policies/                # optional: large definitions referenced by file
│   └── github-hosted-runners.yml
└── projects/
    ├── technology.cbi.yaml           # file name (minus .yaml) = project slug
    └── technology.foo.yaml
```

All file references are relative to the config root.

### 3.1 `organization.yaml`

```yaml
organization_id: 00000000-0000-0000-0000-000000000000
# api_url: https://app.signpath.io/Api     # optional
default_profile: standard

pipeline_policies:                          # managed; key = slug
  github-hosted-runners:
    name: GitHub-hosted runners only
    description: No self-hosted runners, no reruns.
    definition:                             # internal definition, sent as YAML text (max 20 KiB)
      github-build-policies:
        version: '1.0'
        disallow_reruns: true
        runners:
          require_github_hosted: true
  scm-rules:
    name: Branch protection
    definition_file: pipeline-policies/scm-rules.yml
  shared-external:
    name: Externally defined
    external:
      repository: https://github.com/example/policies
      branch: main
      path: .signpath/policies/build.yml    # API enforces the ^\.signpath/policies/ prefix

unmanaged:                                  # never reported as drift, never pruned
  projects: [playground]
  ci_users: [directory-sync, release-automation]
  pipeline_policies: []
```

### 3.2 `profiles/standard.yaml`

```yaml
trusted_build_systems: [github.com]         # slugs, linked to every project using this profile
artifact_configurations:
  default:
    name: Default
    shared: jar-signing                     # artifact-configurations/jar-signing.xml
default_artifact_configuration: default
signing_policies:
  test-signing:
    name: Test signing
    purpose: test
    certificate: test-certificate           # certificate slug (not managed)
    trusted_build_system_verification: true
    origin_verification:
      branches: ['*']
  release-signing:
    name: Release signing
    purpose: release
    certificate: release-certificate
    trusted_build_system_verification: true
    origin_verification:
      branches: [main, 'release/*']
    pipeline_policies:
      github-hosted-runners: enforce        # slug: log | enforce
```

### 3.3 `projects/technology.cbi.yaml` (minimal, inherits `standard`)

```yaml
name: Eclipse CBI
description: Common Build Infrastructure
repositories:
  - https://github.com/eclipse-cbi/cbi
readers: [group:cbi-committers]
ci_users:
  technology.cbi:                           # CI user name, unique across the whole config
    notification_email: cbi-dev@example.org
    signing_policies: [test-signing, release-signing]
signing_policies:
  release-signing:
    approval:
      approvers: [alice@example.org, group:cbi-leads]
      required: 1
```

### 3.4 `projects/technology.foo.yaml` (custom artifact configs, drops a policy)

```yaml
name: Eclipse Foo
profile: standard                           # explicit; defaults to default_profile
repositories: [https://github.com/eclipse-foo/foo]
ci_users:
  technology.foo:
    signing_policies: [release-signing]
artifact_configurations:
  installer:
    name: Windows installer
    file: artifact-configurations/custom/technology.foo-installer.xml
  macos:
    name: macOS bundle
    xml: |
      <artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
        ...
      </artifact-configuration>
signing_policies:
  test-signing: ~                           # remove a policy inherited from the profile
```

### 3.5 Semantics

* **Project slug is the file name.** It is checked against the slug charset and for
  case-insensitive uniqueness.
* **Merging a profile into a project:**
  * Maps are deep-merged by key.
  * Scalars and lists are replaced, never concatenated.
  * `~` removes an inherited map entry.
  * An artifact config's source keys (`shared`, `file`, `xml`) replace each other as
    a group. Exactly one must be set after the merge.
* **Principal references:**
  * A bare email is an interactive user, matched on `accountEmailAddress`,
    case-insensitive (to verify).
  * `group:<name>` is a user group, matched on name.
  * A project's own CI users attach themselves to policies through
    `ci_users.<name>.signing_policies`. `ci:<name>` in `submitters` references a CI
    user the project does not own: one owned by another project, or one listed in
    `unmanaged.ci_users`. It produces a warning, and it lets `import` represent
    shared CI users faithfully
    ([ADR-0007](docs/decisions/0007-declare-ci-users-explicitly-in-their-owning-project.md)).
* **Computing submitters:** a policy's submitters are the CI users that list it,
  plus any explicit `submitters:` (humans or groups). Validation rejects humans as
  submitters when trusted build system verification is on.
* **Artifact config XML is not templated by the tool.** SignPath's own
  `<parameters>` and `${name}` substitution already cover per-project variation.
  Editing a shared XML file updates every project that uses it, and `plan` shows
  every affected project.
* **Every object has `active: true` by default.** `active: false` keeps an object
  declared but deactivated.
* **Editor support:** a JSON Schema is exported (`signpath-manager schema`) for
  yaml-language-server.

## 4. CLI

```text
signpath-manager [global options] <command>

Global:
  -c, --config DIR            config root (default: .)
  --token-file PATH           admin API token from a file (warns if group/world-readable)
  --token-stdin               admin API token from stdin
                              (otherwise from the SIGNPATH_API_TOKEN env var; there is no --token flag)
  --organization-id UUID      override (also SIGNPATH_ORGANIZATION_ID), e.g. for a sandbox org
  --api-url URL
  -v / --debug                Authorization header is always redacted

Commands:
  validate                    offline: schema, merge, references inside the repo, XSD validation of XML
  plan    [--project SLUG]... [--org-only] [--prune] [--output text|json] [--detailed-exitcode]
  apply   [--project SLUG]... [--org-only] [--prune] [--yes]
          [--ci-token-file PATH | --discard-new-ci-tokens]
          [--max-deactivations N] [--parallelism N]
  import  -o DIR [--project SLUG]... [--profile NAME]
  ci-user rotate-token NAME [--ci-token-file PATH]
  schema                      print the JSON Schema of the config files
```

**What each run targets:**

| Run | Org-level objects (pipeline policies, pruning of undeclared projects and CI users) | Projects |
| --- | --- | --- |
| no scope flag | yes | all |
| `--project X` (repeatable) | no | only X: its artifact configs, trusted build system links, CI users and signing policies. Referenced pipeline policies must already exist live, otherwise it fails with "run a full apply / `--org-only` first". |
| `--org-only` | yes | none |

**Preflight on every online command:**

* `GET Users/Me`: show the identity and role, and fail early if the role is too low
  for the command.
* `GET Organization`: show the org name and subscription so the operator sees which
  org is targeted, and check that creations won't exceed `maxNumberOfActiveProjects`.

## 5. Reconciliation engine

`load config` → `resolve` → `fetch live` → `diff` → `plan` → `render` / `execute`

1. **Load and resolve (offline).**
   * Parse YAML with ruamel so errors carry `file:line`.
   * Merge profiles.
   * Load XML and validate it against the vendored `artifact-configuration/v1.xsd`.
   * Check internal references: policy names in `ci_users`, pipeline-policy slugs,
     library names.
   * Produce a fully explicit `DesiredState`.
2. **Fetch live state, scoped and concurrent (httpx async, bounded semaphore, retries
   with backoff on 429 and 5xx).**
   * Org-wide lists, fetched once: Projects, CIUsers, InteractiveUsers, UserGroups,
     Certificates, TrustedBuildSystems, PipelinePolicies.
   * For each in-scope project: the project, each signing policy (with
     `includePipelinePolicyContent`), and each artifact config's XML.
3. **Resolve references against live state.**
   * Emails and group names become UUIDs.
   * Certificate slugs must exist, be active, and have a purpose matching the
     policy's purpose.
   * Trusted build system slugs must exist and be active.
   * Any failure is a plan error, not a runtime error.
4. **Diff** into typed actions: `Create`, `Update(field diffs)`, `Activate`,
   `Deactivate`, `Link`, `UnfixableDrift`.
   * Lists of principals and repository URLs are compared as sets.
   * XML is compared as text: only a leading BOM, line endings and trailing
     whitespace are normalized, because SignPath stores it verbatim.
   * Pipeline-policy definitions are compared as parsed YAML.
5. **Execution order.** Within a project, actions run sequentially; across projects,
   `--parallelism` applies (default 1).
   1. Pipeline policies: create, edit, activate. Org scope only.
   2. Per project:
      1. Project: create (with the default artifact config's XML) or edit; activate
         if needed.
      2. After a create: rename the initial artifact config to its declared slug and
         name.
      3. Artifact configs: add, `Edit` (name, slug, description), `EditXml`,
         activate, then `SetDefaultArtifactConfiguration`.
      4. Trusted build system links: add missing ones. Extra links are
         `UnfixableDrift`.
      5. CI users: create (token goes to the sink), edit, change notification email,
         activate.
      6. Signing policies: add or edit with the full body, activate.
      7. With `--prune`: deactivate undeclared signing policies and artifact configs.
         Never the default artifact config.
   3. Org-level prune (full run with `--prune`): deactivate undeclared projects, CI
      users and pipeline policies.
      * Lists under `unmanaged` are skipped, and so is the authenticated user.
      * The run aborts if more than `--max-deactivations` (default 10) objects would
        be deactivated.
6. **Failure handling.** A failure stops that project and the run continues with
   the others, then exits non-zero. Runs converge, so re-running is the recovery.
7. **Output.**
   * `plan` gives a readable diff (rich) or JSON.
   * `--detailed-exitcode`: 0 no changes, 2 changes, 1 error.
   * `apply` shows the plan and asks for confirmation; on a non-TTY it requires
     `--yes`.

## 6. CI user tokens

* **Interface:** `TokenSink.deliver(ci_user, project, token)`.
* **v1 sinks:**
  * **TTY sink (default when interactive):** writes to `/dev/tty`, not stdout, so the
    token never ends up in logs or pipes. It waits for acknowledgement before
    continuing.
  * **File sink (`--ci-token-file`):** appends JSON lines
    `{ci_user, project, token, created_at}` to a file created with mode 0600.
* **Non-interactive run without a sink:** creating a CI user fails unless
  `--discard-new-ci-tokens` is passed. In that case an operator later runs
  `ci-user rotate-token NAME` to get a usable token.
* **Tokens never appear in** objects, reprs, exceptions or debug logs (pydantic
  `SecretStr` plus httpx hook redaction).
* **Later sinks, not v1:** Vault / pass, GitHub Actions secret.

## 7. Import

`import -o DIR` reads the live org and writes a config root.

* `organization.yaml`: the org ID, plus pipeline policies (internal definitions
  inline, or in `pipeline-policies/` when large).
* **One `projects/<slug>.yaml` per project.**
  * With `--profile` (or an existing profile in `DIR`), only the fields that differ
    from the profile are written, which gives minimal files. Intended workflow: import
    once raw, write the profile by hand from what's common, then import again to get
    minimal files.
* **XML deduplication.** Artifact config XML that is identical after normalization
  in two or more projects goes to `artifact-configurations/imported-<hash8>.xml`
  (operators rename these later). XML used by a single project goes to
  `artifact-configurations/custom/<project>.<config>.xml`.
* **CI users** are assigned to the project whose policies they submit to. A CI user
  that submits to several projects breaks the one-owner rule. It is written to an
  `import-report.md` for manual resolution and left out of every project file.
* **Principals** become `email` and `group:name` references.
* **Inactive objects** come out as `active: false`. Things the config can't express
  go to the report.
* **Acceptance criterion:** running `import` and then `plan` shows **no changes**.
  This is the round-trip test for the whole model.

## 8. Tech stack and layout of this repo

* Python ≥ 3.12, `uv`, src layout, entry point `signpath-manager`.
* Runtime: `click`, `httpx`, `pydantic` v2, `ruamel.yaml`, `lxml`, `rich`.
* Dev: `pytest`, `respx`, `ruff`, `mypy`, `datamodel-code-generator`, `pre-commit`.
* **Vendored:** `resources/signpath-openapi.json` and `artifact-configuration-v1.xsd`.
  * API models are hand-written and lenient rather than generated, because real
    responses deviate from the spec (numeric `status` values,
    [ADR-0010](docs/decisions/0010-hand-write-lenient-api-models.md)). Client
    methods cover the ~40 endpoints used.
  * `scripts/update-spec.sh` refreshes both files, and a scheduled CI job flags
    upstream spec changes.
* Distribution: PyPI and a container image (`ghcr.io/eclipse-csi/signpath-manager`).

```text
src/signpath_manager/
  cli.py
  auth.py            # admin token resolution, SecretStr
  config/            # model.py (pydantic), loader.py, profiles.py, xml.py, schema export
  api/               # client.py, models_generated.py, errors.py, retry/backoff
  live/              # fetch.py -> LiveState
  plan/              # diff.py, actions.py, render.py
  apply/             # executor.py (ordering, failure isolation)
  importer/          # export.py, dedupe.py, minimize.py
  tokens/            # sinks.py
  resources/
tests/
examples/basic/      # generic sample config root (no Eclipse data)
docs/
```

## 9. Testing

* **Unit:** profile merging, validation errors with `file:line`, reference
  resolution, diff engine. Table-driven, live vs desired.
* **API client:** `respx` fixtures recorded from a sandbox org.
* **Golden files:** plan output, text and JSON.
* **End-to-end** against a dedicated sandbox SignPath org (secret-gated workflow):
  * create a project from config, `apply`, then `plan` shows no changes (convergence);
  * `apply` twice: the second run is a no-op (idempotency);
  * `import` then `plan` shows no changes (round-trip);
  * `--prune` with a mass-deactivation guard.

## 10. Milestones

1. **M0, scaffolding:** pyproject, uv, ruff, mypy, pytest, CI, license per eclipse-csi
   conventions, vendored spec and XSD, generated models.
2. **M1, offline model:** config loading, profiles, XML library, XSD validation,
   `validate`, `schema`, `examples/basic`.
3. **M2, read-only:** API client, live fetch, reference resolution, diff, `plan` (text
   and JSON). Usable with a Global Reader token.
4. **M3, apply:** executor, project creation flow, artifact configs, trusted build
   system links, CI users with token sinks, signing policies, `--project` and
   `--org-only` scoping, pipeline policies.
5. **M4, import:** export, XML deduplication, profile-aware minimization, import
   report, round-trip test.
6. **M5, hardening and release:** `--prune` with its guards, `ci-user rotate-token`,
   docs (config reference generated from the schema), a sample GitHub workflow for a
   config repo (PR runs `validate` + `plan` with a reader token, merge runs `apply`),
   PyPI and container release.

M2 → M4 → M3 is an equally valid order if we want to adopt the existing org before
writing anything to it.

## 11. Verified against the sandbox org (2026-11-20)

Probe objects in the sandbox are prefixed `spm-probe-`.

| Question | Answer |
| --- | --- |
| `POST CIUsers` response | `201`, body `{"userId", "apiToken"}`, `Location` header. |
| Initial artifact config of a new project | Slug `initial`, name "Initial version", default. |
| Is XML normalized? | No. Stored and returned byte for byte (comments and whitespace kept), so the diff compares text ([ADR-0004](docs/decisions/0004-share-artifact-configuration-xml-through-a-library-and-compare-it-verbatim.md)). |
| XML validation | Server-side XSD validation with precise messages. The XSD lists more file types than the docs. |
| Slug lookups | Case-insensitive, and slugs are unique case-insensitively. |
| CI user names | Must be unique among **active** users; deactivated users free the name. |
| Deactivated slugs | Can't be reused: the tool must reactivate. Inactive objects can still be edited. |
| Lists and inactive objects | `GET Projects` and project details include inactive policies and artifact configs. The CI user, group and interactive user lists carry no status or email: one GET per user is needed. |
| Default artifact config | Can't be deactivated. |
| `submitterIds` | Must not be empty. |
| Certificates | An expired or CSR-pending certificate is rejected ("not available for signing"). |
| Purpose vs certificate | A mismatch is accepted but the policy becomes invalid, with numeric `status: 64` in the detail GET (an expired certificate gives `8`). |
| Interactive submitter with trusted build system required | Accepted by the API despite the docs, so the tool warns. |
| Origin verification without a trusted build system | Rejected. |
| Approvals | `numberOfRequiredApprovals` must not exceed the number of active approvers. |
| Omitting `trustedBuildSystemVerification` on edit | Disables it. Omitting `readerIds` / `configuratorIds` clears them. |
| CI users as configurators | Rejected ("CI users cannot be configurators"). |
| Linking an already linked trusted build system | `400`; the slug lookup is case-insensitive. |
| Interactive user matching | `accountEmailAddress` (absent while an invitation is pending), `identity` (`<provider>:<email>`), notification email (confirmed or unconfirmed). |
| `allowedBuildDefinitions` | Free strings, for example `.github/workflows/release.yml`. Branch patterns are free strings (`main`, `release/*`, `**`). |
| Rate limits | No `429` with 40 concurrent GETs; no rate-limit headers. |
| Conditional requests | No `ETag`, `Last-Modified` or `Cache-Control`; `If-None-Match`, `If-Modified-Since` and `If-Match` are ignored. See [ADR-0011](docs/decisions/0011-use-conditional-requests-and-emulate-write-preconditions.md). |
| Subscription | `EnterpriseSandbox`. **Pipeline policies and disabling malware scanning are disabled** (`403` "feature is disabled for this subscription"). |

Still open:

1. Pipeline policy behavior (definition normalization, validation): not testable in
   the sandbox, so implemented from the spec and covered by the fake API only.
2. Minimum role for each endpoint (for example, whether Global Reader can read CI
   users and certificates).
3. Whether the production Enterprise subscription includes pipeline policies.

## 11b. Conditional requests

The tool is a good API citizen even though SignPath supports no HTTP validators today
([ADR-0011](docs/decisions/0011-use-conditional-requests-and-emulate-write-preconditions.md)):

* **GET:** the client keeps `ETag` and `Last-Modified` per URL and sends
  `If-None-Match` / `If-Modified-Since`, serving the cached body on `304`. This is
  inert today and starts working when SignPath emits validators.
* **Writes:** the client re-reads the resource right before writing and compares a
  fingerprint with the state the plan was computed from. On mismatch the action
  fails as a stale plan. A known `ETag` is also sent as `If-Match`.
* **Traffic:** only differing fields trigger writes. Concurrency is bounded,
  `Retry-After` is honored on `429` and `503` with exponential backoff, connections
  are reused, and a `User-Agent` names the tool.

## 12. Out of scope for v1

* Certificates.
* Creating or editing interactive users and groups, global roles, directory sync.
* Trusted build system lifecycle.
* Webhooks and notification settings (not in the API).
* Audit-log consumption.
* Vault and GitHub token sinks.
* Pulling metadata from Eclipse PMI. That would be EF-specific; it could be a
  separate generator that writes project files, keeping this tool generic.
