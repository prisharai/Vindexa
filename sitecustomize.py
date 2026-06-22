"""Project-local Python startup fixes.

The uv-provided macOS Python in this workspace can segfault when pytest imports
the native ``readline`` module during its capture setup. Pytest only imports it
as a best-effort stdio workaround, so a stub is enough to keep the documented
``uv run pytest`` command usable without affecting the application runtime.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


def _running_pytest() -> bool:
    return any(Path(arg).name in {"pytest", "py.test"} for arg in sys.argv)


if _running_pytest() and "readline" not in sys.modules:
    sys.modules["readline"] = types.ModuleType("readline")
