from __future__ import annotations

import shutil
import sys
from pathlib import Path


def uv_bin() -> str | None:
    return shutil.which("uv")


def skill_script_command(script_path: Path) -> list[str]:
    uv = uv_bin()
    if uv:
        return [uv, "run", str(script_path)]
    return [sys.executable, str(script_path)]
