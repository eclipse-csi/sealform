"""Where newly issued CI user API tokens go (see ADR-0008).

Tokens never transit through stdout, logs, plans or exceptions.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TextIO

from pydantic import SecretStr


class TokenSinkError(Exception):
    pass


class TokenSink(Protocol):
    description: str

    def deliver(self, project: str | None, ci_user: str, token: SecretStr) -> None: ...


class FileSink:
    """Append JSON lines to a file only the current user can read."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.description = f"file {path}"
        if path.exists():
            mode = path.stat().st_mode
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                raise TokenSinkError(f"{path} is accessible by group or others; chmod 600 it first")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        except OSError as e:
            raise TokenSinkError(f"cannot open {path}: {e.strerror}") from e
        os.close(fd)

    def deliver(self, project: str | None, ci_user: str, token: SecretStr) -> None:
        record = {
            "project": project,
            "ci_user": ci_user,
            "token": token.get_secret_value(),
            "issued_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())


class TtySink:
    """Show the token on the controlling terminal (not stdout) and wait for acknowledgement."""

    description = "terminal"

    def __init__(self, tty_out: TextIO | None = None, tty_in: TextIO | None = None) -> None:
        self._out = tty_out
        self._in = tty_in
        if tty_out is None:
            try:
                with open("/dev/tty", "w", encoding="utf-8"):
                    pass
            except OSError as e:
                raise TokenSinkError("no terminal available to show new CI user tokens") from e

    def deliver(self, project: str | None, ci_user: str, token: SecretStr) -> None:
        out = self._out or open("/dev/tty", "w", encoding="utf-8")  # noqa: SIM115
        inp = self._in or open("/dev/tty", encoding="utf-8")  # noqa: SIM115
        try:
            where = f" (project {project})" if project else ""
            out.write(
                f"\n=== New API token for CI user '{ci_user}'{where} ===\n"
                f"{token.get_secret_value()}\n"
                "Store it now (for example as the SIGNPATH_API_TOKEN secret of the project's CI);\n"
                "it is not shown again. Press Enter to continue... "
            )
            out.flush()
            inp.readline()
            out.write("\033[3A\033[2K" if out.isatty() else "")  # best effort: move away from the token
            out.flush()
        finally:
            if self._out is None:
                out.close()
            if self._in is None:
                inp.close()


class DiscardSink:
    """Explicitly requested: drop the token; recover later with `ci-user rotate-token`."""

    description = "discarded (--discard-new-ci-tokens)"

    def __init__(self) -> None:
        self.discarded: list[str] = []

    def deliver(self, project: str | None, ci_user: str, token: SecretStr) -> None:
        self.discarded.append(ci_user)
