"""The CV source seam: parses base_cv.md into sections and entries.

Only this module knows the base CV's markdown layout. It never rewrites
content — parsing preserves anchors (entry headers, date lines, project
tech lines) verbatim so downstream tailoring cannot alter facts.
"""
import re
from dataclasses import dataclass

from config import TAILORED_SECTIONS

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")
_ENTRY_RE = re.compile(r"^###\s+")
_BULLET_RE = re.compile(r"^-\s+")


@dataclass(frozen=True)
class Entry:
    anchor: str          # verbatim: the ### line plus any non-bullet lines under it
    bullets: list[str]   # each bullet without its leading "- "


@dataclass(frozen=True)
class Section:
    name: str
    is_tailored: bool
    body: str            # cleaned text; used for static sections and About Me/Skills
    entries: list[Entry]  # populated only for tailored sections that contain ### entries


@dataclass(frozen=True)
class ParsedResume:
    preamble: str            # verbatim block above the first ## section
    sections: list[Section]  # in file order

    def get(self, name: str) -> "Section | None":
        for section in self.sections:
            if section.name == name:
                return section
        return None


def _clean(lines: list[str]) -> str:
    """Drop standalone --- rules and trim surrounding blank lines."""
    kept = [ln for ln in lines if ln.strip() != "---"]
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(ln.rstrip() for ln in kept)


def _build_entry(lines: list[str]) -> Entry:
    anchor_lines: list[str] = []
    bullets: list[str] = []
    in_bullets = False
    for ln in lines:
        if _BULLET_RE.match(ln):
            in_bullets = True
            bullets.append(_BULLET_RE.sub("", ln).strip())
        elif not in_bullets and ln.strip() and ln.strip() != "---":
            anchor_lines.append(ln.rstrip())
    return Entry(anchor="\n".join(anchor_lines), bullets=bullets)


def _parse_entries(lines: list[str]) -> list[Entry]:
    entries: list[Entry] = []
    current: "list[str] | None" = None
    for ln in lines:
        if _ENTRY_RE.match(ln):
            if current is not None:
                entries.append(_build_entry(current))
            current = [ln]
        elif current is not None:
            current.append(ln)
    if current is not None:
        entries.append(_build_entry(current))
    return entries


def parse_resume(text: str) -> ParsedResume:
    lines = text.splitlines()

    idx = 0
    while idx < len(lines) and not _SECTION_RE.match(lines[idx]):
        idx += 1
    preamble = _clean(lines[:idx])

    sections: list[Section] = []
    while idx < len(lines):
        name = _SECTION_RE.match(lines[idx]).group(1)
        idx += 1
        body_lines: list[str] = []
        while idx < len(lines) and not _SECTION_RE.match(lines[idx]):
            body_lines.append(lines[idx])
            idx += 1
        is_tailored = name in TAILORED_SECTIONS
        has_entries = any(_ENTRY_RE.match(ln) for ln in body_lines)
        entries = _parse_entries(body_lines) if (is_tailored and has_entries) else []
        sections.append(
            Section(name=name, is_tailored=is_tailored, body=_clean(body_lines), entries=entries)
        )
    return ParsedResume(preamble=preamble, sections=sections)
