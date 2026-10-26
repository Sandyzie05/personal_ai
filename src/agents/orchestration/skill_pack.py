"""Ships the built-in skills (repo `skills/*.md`) into the user's skills dir, never clobbering edits."""

from pathlib import Path
from typing import List

from src.agents.skills import skills_dir

PACK_DIR = Path(__file__).resolve().parents[3] / "skills"


def install_builtin_skills() -> List[str]:
    """Copy missing skill files; existing files (possibly user-edited) are left untouched."""
    target = skills_dir()
    installed = []
    for src in sorted(PACK_DIR.glob("*.md")):
        dest = target / src.name
        if not dest.exists():
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            installed.append(src.stem)
    return installed
