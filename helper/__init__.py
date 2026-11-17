"""The release version is a package resource, shared by source and image builds."""

from importlib.resources import files as _resource_files
import re as _re


def _read_version():
    value = _resource_files(__name__).joinpath("VERSION").read_text(encoding="ascii").rstrip("\r\n")
    if not _re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
        raise RuntimeError("helper/VERSION must contain a numeric MAJOR.MINOR.PATCH release version")
    return value


__version__ = _read_version()
