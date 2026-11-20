---
status: accepted
date: 2026-11-20
---

# Share artifact configuration XML through a library and compare it verbatim

## Context and Problem Statement

SignPath artifact configurations are XML documents that belong to a single project; the API has no organization-level artifact configuration.
Many Eclipse projects sign the same kind of artifacts, a few need custom configurations.
How should XML be shared, and how should the tool decide whether the XML in SignPath is up to date?

## Decision Drivers

* Default artifact configurations must be easy to reference from every project.
* Some projects need custom XML.
* Probing the sandbox showed SignPath stores and returns the XML byte for byte, including comments and whitespace.
* SignPath already supports per-request `<parameters>` with `${name}` substitution.

## Considered Options

* Library of XML files referenced by name, plus per-project `file` or inline `xml`, compared as text
* Library with tool-side templating, compared after canonicalization

## Decision Outcome

Chosen option: "Library of XML files referenced by name, plus per-project `file` or inline `xml`, compared as text", because SignPath stores the XML verbatim, so "what is in the repository is what is in SignPath" can be guaranteed exactly, and SignPath parameters already cover per-project variation.

An artifact configuration has exactly one source: `shared: <name>` (the file `artifact-configurations/<name>.xml`), `file: <path>` or `xml: <inline text>`.
Comparison normalizes only a leading byte order mark, line endings and trailing whitespace at the end of the document.
XML is validated locally against the vendored `artifact-configuration/v1.xsd` before any API call.

### Consequences

* Good, because a change to a shared XML file updates every project using it, and `plan` lists each one.
* Good, because comment-only changes are applied too, which keeps the UI and the repository identical.
* Bad, because a reformatting of a shared file produces updates in every project using it.

### Confirmation

Round-trip tests: `import` followed by `plan` reports no change.

## Pros and Cons of the Options

### Library of XML files referenced by name, plus per-project `file` or inline `xml`, compared as text

* Good, because there is no extra templating language.
* Bad, because cosmetic changes are not ignored.

### Library with tool-side templating, compared after canonicalization

* Good, because projects could vary small parts of a shared XML.
* Bad, because it duplicates SignPath's own parameter mechanism and hides the XML actually stored.
