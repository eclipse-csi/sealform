---
status: accepted
date: 2026-11-20
---

# Rename the project to sealform

## Context and Problem Statement

The tool was created as `signpath-manager`.
Using the SignPath name as the name of the tool probably violates the SignPath trademark, or at least suggests that the tool is endorsed or maintained by the company behind SignPath, which it is not.
Should the project keep its name?

## Decision Drivers

* The name must not suggest an affiliation with, or endorsement by, the makers of SignPath.
* The name must be free on PyPI and in the `eclipse-csi` GitHub organization (`sealform` was free on both on 2026-11-20).
* Nothing has been released yet, so a rename costs no migration for users.

## Considered Options

* Rename to sealform
* Keep signpath-manager with a non-affiliation disclaimer

## Decision Outcome

Chosen option: "Rename to sealform", because a name without the SignPath mark removes the implied endorsement instead of only disclaiming it, and the project has not been published under the old name.

The distribution, the Python package, the command, the User-Agent and the GitHub repository (`eclipse-csi/sealform`) use the new name.
SignPath is still named where the tool describes what it manages ("manage SignPath.io organizations as code"), and the environment variables keep their `SIGNPATH_` prefix (`SIGNPATH_API_TOKEN`, `SIGNPATH_ORGANIZATION_ID`, `SIGNPATH_API_URL`) because they name the credentials and endpoint of the SignPath service.
The README states that the project is not affiliated with SignPath.

### Consequences

* Good, because the tool no longer presents itself under someone else's mark.
* Good, because no published package, image or configuration has to migrate.
* Neutral, because `SIGNPATH_` environment variables remain, as descriptions of the service they configure.
* Bad, because the GitHub repository and existing clones must be renamed; old links rely on GitHub's redirects.
* Bad, because the name no longer says what the tool manages, so discoverability relies on the description and keywords.

### Confirmation

`git grep -i -E 'signpath[-_]manager'` only finds historical mentions (this ADR and PLAN.md).

## Pros and Cons of the Options

### Rename to sealform

* Good, because it avoids the trademark question altogether.
* Bad, because the repository, clones and documentation links change.

### Keep signpath-manager with a non-affiliation disclaimer

* Good, because nothing has to change.
* Bad, because the name itself still suggests endorsement, and a disclaimer does not travel with the package name on PyPI or in shell commands.

## More Information

The trademark status was not reviewed legally; the rename is a precaution.
