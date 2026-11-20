"""Resolution of the administrator API token (see ADR-0012)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import TextIO

from pydantic import SecretStr

ENV_TOKEN = "SIGNPATH_API_TOKEN"  # noqa: S105 - environment variable name


class TokenError(Exception):
    pass


def resolve_token(
    token_file: Path | None,
    token_stdin: bool,
    *,
    stdin: TextIO | None = None,
    environ: dict[str, str] | None = None,
    warn: list[str] | None = None,
) -> SecretStr:
    """Return the API token from --token-file, --token-stdin or $SIGNPATH_API_TOKEN, in that order."""
    environ = dict(os.environ) if environ is None else environ
    if token_file is not None:
        try:
            mode = token_file.stat().st_mode
            raw = token_file.read_text(encoding="utf-8")
        except OSError as e:
            raise TokenError(f"cannot read token file {token_file}: {e.strerror}") from e
        if warn is not None and mode & (stat.S_IRWXG | stat.S_IRWXO):
            warn.append(f"token file {token_file} is accessible by group or others (chmod 600 recommended)")
        value = raw.strip()
        source = f"token file {token_file}"
    elif token_stdin:
        value = (stdin or sys.stdin).readline().strip()
        source = "stdin"
    elif environ.get(ENV_TOKEN):
        value = environ[ENV_TOKEN].strip()
        source = f"${ENV_TOKEN}"
    else:
        raise TokenError(f"no API token: set ${ENV_TOKEN}, or use --token-file PATH or --token-stdin")
    if not value:
        raise TokenError(f"empty API token from {source}")
    if any(c.isspace() for c in value):
        raise TokenError(f"API token from {source} contains whitespace")
    return SecretStr(value)
