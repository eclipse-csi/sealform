---
status: accepted
date: 2026-11-20
---

# Reference artifact configuration XML by file path

## Context and Problem Statement

[ADR-0004](0004-share-artifact-configuration-xml-through-a-library-and-compare-it-verbatim.md) introduced two kinds of references to artifact configuration XML files: `shared: <name>` for a library in `artifact-configurations/`, and `file: <path>` for any other file, next to inline `xml:`.
In practice `shared: <name>` was only a shorthand for `file: artifact-configurations/<name>.xml`: whether XML is shared depends on how many projects reference a file, not on how the reference is written.
Should the configuration keep two kinds of file references?

## Decision Drivers

* One mechanism is easier to learn, document and merge (profiles replace an artifact configuration's source as a group).
* `import` should reuse any XML file already in the configuration, wherever it lives.
* SignPath stores artifact configuration XML verbatim, and its own `<parameters>` cover per-request variation (unchanged from ADR-0004).
* Breaking the configuration format is cheap now: 0.1.0 has just been released and no configuration repository exists yet.

## Considered Options

* File references only
* Keep shared and file references

## Decision Outcome

Chosen option: "File references only", because `shared` added a second concept without adding a capability, and dropping it before the first configuration repository exists costs almost nothing.

An artifact configuration has exactly one source: `file: <path>` (relative to the configuration root, which it cannot leave) or inline `xml:`.
The layout is a convention: XML used by several projects lives in `artifact-configurations/`, project-specific XML in `artifact-configurations/custom/`; `import` follows it, names common files after the artifact configuration slug when that name is free, and reuses any existing file with the same XML.
Each file is read and validated once against the vendored XSD, however many projects reference it.
The comparison rule of ADR-0004 is unchanged: only a leading byte order mark, line endings and trailing whitespace at the end of the document are normalized, and a document without an XML declaration is compared as stored by SignPath, with `<?xml version="1.0" encoding="utf-8"?>` prepended.

### Consequences

* Good, because there is one way to reference a file, and profiles merge one source group (`file`, `xml`).
* Good, because `import` reuses XML files the team moved or renamed, not only top-level library files.
* Neutral, because the folder layout is a convention the tool does not enforce.
* Bad, because references are longer (`file: artifact-configurations/jar-signing.xml` instead of `shared: jar-signing`).
* Bad, because configurations written with `shared:` must be migrated; the loader names the replacement in its error message.

### Confirmation

Loader tests cover file references, single validation of a shared file and the `shared:` migration message; import tests cover reuse of existing files, common file naming and round trips.

## Pros and Cons of the Options

### File references only

* Good, because it is one concept.
* Bad, because references are a little longer.

### Keep shared and file references

* Good, because short names read well in project files.
* Bad, because the distinction carries no meaning the tool can rely on, and a file can be shared through `file:` anyway.

## More Information

Supersedes [ADR-0004](0004-share-artifact-configuration-xml-through-a-library-and-compare-it-verbatim.md); its comparison rule is carried over above.
Narrows the artifact configuration source group of the merge rules in [ADR-0003](0003-share-project-defaults-through-organization-level-profiles.md) to `file` and `xml`.
