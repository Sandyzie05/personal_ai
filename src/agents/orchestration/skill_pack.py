"""Ships the built-in skills (repo `skills/*.md`) into the user's skills dir, never clobbering edits."""

import hashlib
from pathlib import Path
from typing import List

from src.agents.skills import skills_dir

PACK_DIR = Path(__file__).resolve().parents[3] / "skills"
# sha256 of earlier shipped versions: an installed copy still equal to one of these was never edited, so it is upgraded.
PREVIOUS_VERSIONS = {
    "forecast-critic.md": {"3a3035c73d5eae9bc97f809bee68eba70f227af1681f3a9d2c2692a9cc477be8"},
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def install_builtin_skills() -> List[str]:
    """Copy missing skill files and upgrade unedited old shipped copies; user-edited files are left untouched."""
    target = skills_dir()
    installed = []
    for src in sorted(PACK_DIR.glob("*.md")):
        dest = target / src.name
        new = src.read_text(encoding="utf-8")
        if not dest.exists() or _sha(dest.read_text(encoding="utf-8")) in PREVIOUS_VERSIONS.get(src.name, ()):
            dest.write_text(new, encoding="utf-8")
            installed.append(src.stem)
    return installed
