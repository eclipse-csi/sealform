# Configuration reference

A configuration root describes one SignPath organization:

```text
<root>/
├── organization.yaml          # organization ID, default profile, pipeline policies, unmanaged objects
├── profiles/<name>.yaml       # shared project defaults
├── artifact-configurations/   # shared XML library: <name>.xml is referenced as `shared: <name>`
│   └── custom/                # convention for project-specific XML files
├── pipeline-policies/         # optional: pipeline policy definitions referenced by file
└── projects/<slug>.yaml       # one file per project; the file name is the project slug
```

File references (`file:`, `definition_file:`) are relative to the root and cannot leave it.
Slugs use ASCII letters, digits, `.`, `-` and `_`, and are compared case-insensitively, like SignPath does.
JSON Schemas for editor completion are in [schemas/](../schemas); add a
`# yaml-language-server: $schema=...` comment at the top of a file to use them.

## organization.yaml

```yaml
organization_id: 00000000-0000-0000-0000-000000000000
api_url: https://app.signpath.io/Api        # optional
default_profile: standard                     # optional; used by projects without a `profile` key

pipeline_policies:                            # managed pipeline policies, keyed by slug
  github-hosted-runners:
    name: GitHub-hosted runners only
    description: optional
    definition:                               # inline definition, sent as YAML (max 20480 characters)
      github-build-policies:
        version: '1.0'
        runners: {require_github_hosted: true}
  from-file:
    name: From a file
    definition_file: pipeline-policies/from-file.yml
  external:
    name: Stored in a repository
    external:
      repository: https://github.com/example/policies
      branch: main                            # optional
      path: .signpath/policies/build.yml      # must start with .signpath/policies/
  retired:
    name: Retired
    definition: {github-build-policies: {version: '1.0'}}
    active: false                             # keep declared but deactivated

unmanaged:                                    # never reported as drift, never pruned
  projects: [playground]
  ci_users: [release-automation]
  pipeline_policies: []
```

Exactly one of `definition`, `definition_file` and `external` is required.
Definitions are compared as parsed YAML, so formatting changes are not reported.

## Projects

```yaml
name: Example Alpha                          # required
description: optional
profile: standard                            # optional; default_profile when absent, null for none
repositories: [https://github.com/example/alpha]
readers: [alice@example.org, group:Alpha committers]
configurators: [bob@example.org]
trusted_build_systems: [GitHub.com]          # slugs of existing trusted build systems, linked to the project
active: true                                 # false keeps the project declared but deactivated

artifact_configurations:
  default:
    name: Default                            # defaults to the slug
    description: optional
    shared: jar-signing                      # artifact-configurations/jar-signing.xml
  installer:
    file: artifact-configurations/custom/example.alpha-installer.xml
  inline:
    xml: |
      <artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
        <msi-file><authenticode-sign /></msi-file>
      </artifact-configuration>
default_artifact_configuration: default      # optional when there is a single artifact configuration

ci_users:                                    # CI users owned by this project (names are unique across all projects)
  example.alpha:
    description: optional
    notification_email: alpha-dev@example.org
    signing_policies: [test-signing, release-signing]   # policies this CI user submits to
    active: true

signing_policies:
  release-signing:
    name: Release signing                    # defaults to the slug
    description: optional
    purpose: release                         # test | release; must match the certificate's purpose
    certificate: release-certificate         # slug of an existing, valid certificate
    submitters: [carol@example.org]          # in addition to this project's CI users listing the policy
    approval:
      approvers: [alice@example.org, group:Alpha leads]
      required: 1
    trusted_build_system_verification: true
    origin_verification:                     # requires trusted_build_system_verification
      branches: [main, 'release/*']
      build_definitions: [.github/workflows/release.yml]
    pipeline_policies:                       # requires trusted_build_system_verification
      github-hosted-runners: enforce         # log | enforce
    disable_malware_scanning: false          # needs a subscription that allows it
    active: true
```

Descriptions are compared without regard to line endings: SignPath's web UI stores Windows line endings,
which YAML block scalars (`|`) cannot carry, so `import` writes them with `\n` and `plan` does not report the difference.

### Principals

| Reference | Meaning |
| --- | --- |
| `alice@example.org` | Interactive user, matched by account email, then the email of the login identity, then the notification email. The user must already exist. |
| `group:<name>` | User group, matched by name. |
| `ci:<name>` | Only in `submitters`: a CI user this project does not own. It must be declared by another project (a warning is shown) or listed in `unmanaged.ci_users`. |

Inside a managed object the configuration is authoritative: undeclared readers, configurators, submitters and approvers are removed.

### Rules checked before any API call

* Every active signing policy has at least one submitter.
* Origin verification and pipeline policies require `trusted_build_system_verification: true`.
* Trusted build system verification requires at least one entry in `trusted_build_systems`.
* `approval.required` is at most the number of approvers.
* The default artifact configuration is declared and active.
* XML is well formed and valid against the SignPath artifact configuration XSD.
* A person or group as submitter of a policy that requires a trusted build system produces a warning:
  SignPath documents that only CI users may submit then.

`plan` also checks against the live organization:

* every reference resolves to an existing user, group, CI user, certificate, trusted build system and pipeline policy;
* certificates are valid and their purpose matches;
* the project quota is respected.

## Profiles

A profile file has the same keys as a project except `name`, `description`, `profile`, `repositories`, `ci_users` and `active`.
Merge rules ([ADR-0003](decisions/0003-share-project-defaults-through-organization-level-profiles.md)):

* maps merge deeply by key;
* lists and scalars from the project replace the inherited value;
* `null` removes an inherited entry, at any depth (`test-signing: null`, `origin_verification: null`, `pipeline_policies: {gh: null}`);
* the source keys of an artifact configuration (`shared`, `file`, `xml`) replace each other as a group.

`plan` shows the resolved values, so the effect of a profile change on every project is visible before applying it.

## Artifact configuration XML

SignPath stores the XML exactly as sent, except that a document without an XML declaration gets
`<?xml version="1.0" encoding="utf-8"?>` prepended.
The tool compares XML the same way, so comment and whitespace changes are applied and shown as a diff.
Use SignPath's own `<parameters>` and `${name}` substitution for per-request variations rather than copying XML.
