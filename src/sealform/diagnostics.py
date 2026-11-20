"""Errors and warnings collected while validating configuration or computing a plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Location:
    file: Path
    line: int | None = None  # 1-based
    column: int | None = None  # 1-based

    def __str__(self) -> str:
        if self.line is None:
            return str(self.file)
        if self.column is None:
            return f"{self.file}:{self.line}"
        return f"{self.file}:{self.line}:{self.column}"


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    message: str
    location: Location | None = None
    path: str | None = None  # dotted path inside the document, e.g. signing_policies.release.certificate

    def __str__(self) -> str:
        where = f"{self.location}: " if self.location else ""
        what = f"{self.path}: " if self.path else ""
        return f"{where}{self.severity.value}: {what}{self.message}"


@dataclass
class Diagnostics:
    items: list[Diagnostic] = field(default_factory=list)

    def error(self, message: str, location: Location | None = None, path: str | None = None) -> None:
        self.items.append(Diagnostic(Severity.ERROR, message, location, path))

    def warning(self, message: str, location: Location | None = None, path: str | None = None) -> None:
        self.items.append(Diagnostic(Severity.WARNING, message, location, path))

    def extend(self, other: Diagnostics) -> None:
        self.items.extend(other.items)

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.items if d.severity is Severity.WARNING]

    @property
    def has_errors(self) -> bool:
        return any(d.severity is Severity.ERROR for d in self.items)
