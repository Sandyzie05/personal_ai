"""Skills: reusable instruction blocks that agents can attach.

A skill is a single markdown file at ``<skills_dir>/<slug>.md`` with a
tiny two-field frontmatter block::

    ---
    name: Trading Discipline
    description: Force confirmation and conservative framing for trade actions
    ---
    Before recommending any trade, state position size, estimated cost...

Skills are *instructions*, not personal data, so they live in a plain
directory (default ``~/.personal_ai/skills``), not the encrypted vault -
the same accepted tradeoff as the ``.chroma`` index (see
docs/security_design.md). The directory is overridable via
``PERSONAL_AI_SKILLS_DIR`` (src/config.py).

The frontmatter is parsed with a few lines of string handling rather than
a yaml dependency: only the two fields above are understood, unknown keys
are kept in `meta` untouched.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.config import SKILLS_DIR


def skills_dir() -> Path:
    directory = Path(SKILLS_DIR).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def slugify(name: str) -> str:
    """Filesystem-safe slug for a skill name."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "skill"


def _parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """Split '---\\nkey: value\\n---\\nbody' into (meta, body).

    No frontmatter -> ({}, whole text). Malformed block -> the block is
    treated as body (fail open for a *instructions* file, fail closed
    nowhere - there is no data to protect here).
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: Dict[str, Any] = {}
    close_index = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            close_index = i
            break
        if ":" in lines[i]:
            key, _, value = lines[i].partition(":")
            meta[key.strip()] = value.strip()
    if close_index is None:
        return {}, text
    body = "\n".join(lines[close_index + 1 :]).lstrip("\n")
    return meta, body


def read_skill(slug: str) -> Optional[Dict[str, Any]]:
    """Read one skill: {slug, name, description, body, meta} or None."""
    path = skills_dir() / f"{slug}.md"
    if not path.is_file():
        return None
    meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    return {
        "slug": slug,
        "name": meta.get("name", slug),
        "description": meta.get("description", ""),
        "body": body.strip(),
        "meta": meta,
    }


def list_skills() -> List[Dict[str, Any]]:
    """All skills, sorted by name. Each entry has slug/name/description
    (body omitted - list views shouldn't pull every file's contents)."""
    skills = []
    for path in sorted(skills_dir().glob("*.md")):
        slug = path.stem
        meta, _ = _parse_frontmatter(path.read_text(encoding="utf-8"))
        skills.append(
            {
                "slug": slug,
                "name": meta.get("name", slug),
                "description": meta.get("description", ""),
            }
        )
    skills.sort(key=lambda s: s["name"].lower())
    return skills


def write_skill(slug: str, name: str, description: str, body: str) -> str:
    """Create or overwrite a skill file; returns its slug."""
    slug = slug or slugify(name)
    frontmatter = f"---\nname: {name}\ndescription: {description}\n---\n\n"
    (skills_dir() / f"{slug}.md").write_text(
        frontmatter + body.strip() + "\n", encoding="utf-8"
    )
    return slug


def delete_skill(slug: str) -> bool:
    path = skills_dir() / f"{slug}.md"
    if path.is_file():
        path.unlink()
        return True
    return False


def render_skills_for_prompt(slugs: List[str]) -> str:
    """Assemble the system-prompt block for the given skill slugs.

    Missing slugs are skipped (a deleted skill must not break the agent).
    Returns "" when nothing renders - callers concatenate with their own
    separator only when non-empty.
    """
    sections = []
    for slug in slugs or []:
        skill = read_skill(slug)
        if not skill:
            continue
        section = f"## Skill: {skill['name']}"
        if skill["description"]:
            section += f"\n{skill['description']}"
        if skill["body"]:
            section += f"\n{skill['body']}"
        sections.append(section)
    return "\n\n".join(sections)
