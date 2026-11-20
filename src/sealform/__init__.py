"""Manage SignPath.io organizations as code."""

from importlib.metadata import PackageNotFoundError, version

try:
    # set at build time from the latest vX.Y.Z git tag (uv-dynamic-versioning)
    __version__ = version("sealform")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"
