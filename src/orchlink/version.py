"""Package version helpers.

The canonical version source is ``pyproject.toml``. Installed package metadata
is used only when running from an installed wheel without a source tree.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path
import tomllib

PACKAGE_NAME = "orchlink"


def _source_tree_version() -> str:
    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            version = data.get("project", {}).get("version")
            if isinstance(version, str) and version:
                return version
    return "0+unknown"


def get_version() -> str:
    """Return the Orchlink version from one canonical source.

    In a source checkout, read ``pyproject.toml`` directly so editable installs
    with stale metadata do not create a second version source. In an installed
    wheel where ``pyproject.toml`` is absent, use package metadata generated
    from that same project version.
    """
    source_version = _source_tree_version()
    if source_version != "0+unknown":
        return source_version
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return source_version


__version__ = get_version()


__all__ = ["PACKAGE_NAME", "__version__", "get_version"]
