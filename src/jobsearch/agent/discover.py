"""Turning any user's Canva design into a profile the agent can tailor.

Reading a design gives text boxes with positions. What it does not give is
meaning: which box is the summary, which holds the second job's bullets. That
judgement is made once, at setup, and then frozen — re-deciding it every morning
would let the same CV tailor differently on different days, which is exactly the
inconsistency the guards exist to prevent.

Only `summary`, `skills` and `experience.N.bullets` are ever rewritten. The other
labels exist because base_cv.md needs job titles and dates to be written at all,
even though nothing ever edits them.
"""
import re

from jobsearch.config import EXPERIENCE_SECTION, SKILLS_SECTION, SUMMARY_SECTION
from jobsearch.resume.base_cv import Entry, ParsedResume, Section

# The roles the agent may rewrite.
EDITABLE = ("summary", "skills")
# Labels that carry no index. Entry labels are matched separately.
LABELS = ("summary", "skills", "name", "contact", "other")
_ENTRY_RE = re.compile(r"^experience\.(\d+)\.(title|dates|bullets)$")


def reading_order(elements):
    """(element_id, element) pairs, top to bottom then left to right.

    Canva returns elements in creation order, which bears no relation to how the
    page reads. Position is the only reliable ordering.
    """
    return sorted(elements.items(),
                  key=lambda item: (item[1]["top"], item[1]["left"]))


def _is_label(label):
    return label in LABELS or _ENTRY_RE.match(label or "") is not None


def structural_problems(labels, elements):
    """Everything about this design the agent cannot handle, in the user's words.

    Refusing is deliberate. A structure that is half-supported produces a CV that
    is wrong in ways nothing downstream detects — Canva reports success for a
    replacement that matched nothing.
    """
    found = []
    for _element_id, label in sorted(labels.items()):
        if not _is_label(label):
            found.append(
                f"unrecognised section {label!r}; the agent has no rule for it")

    counts = {}
    for label in labels.values():
        counts[label] = counts.get(label, 0) + 1

    if counts.get("skills", 0) > 1:
        found.append(
            f"your skills appear to be in {counts['skills']} separate boxes; the "
            f"agent needs them in one")
    elif not counts.get("skills"):
        found.append("no skills block identified")

    if counts.get("summary", 0) > 1:
        found.append("more than one summary block identified")
    elif not counts.get("summary"):
        found.append("no summary block identified")

    entries = {}
    for label in labels.values():
        match = _ENTRY_RE.match(label or "")
        if match and match.group(2) == "bullets":
            entries[match.group(1)] = entries.get(match.group(1), 0) + 1
    if not entries:
        found.append("no experience entry identified")
    for index, count in sorted(entries.items()):
        if count > 1:
            found.append(
                f"each bullet in experience entry {int(index) + 1} is a separate "
                f"text box; the agent needs a job's bullets in one box")
    return found


def _text(element):
    """The element's text: regions joined, blank lines dropped."""
    joined = "".join(element.get("regions") or [])
    return "\n".join(line.strip() for line in joined.splitlines() if line.strip())


def build_profile(labels, elements, design_id, page_id, design_title):
    """The profile `jobs init` writes: editable slots, everything else locked."""
    slots, locked = {}, []
    for element_id, _element in reading_order(elements):
        label = labels.get(element_id, "other")
        match = _ENTRY_RE.match(label or "")
        editable = label in EDITABLE or (match and match.group(2) == "bullets")
        if editable:
            slots[label] = element_id
        else:
            locked.append(element_id)
    return {"design_id": design_id, "page_id": page_id,
            "design_title": design_title, "slots": slots, "locked": locked}


def build_resume(labels, elements):
    """A ParsedResume assembled from labelled blocks, ready for render_base_cv.

    Canonical section names regardless of what the design calls them: the parser
    and the guards keep one contract, and only this function has to know that a
    user's "Profile" heading means About Me.
    """
    by_label = {}
    for element_id, element in reading_order(elements):
        by_label.setdefault(labels.get(element_id, "other"), []).append(element)

    def first(label):
        found = by_label.get(label)
        return _text(found[0]) if found else ""

    entries = []
    indexes = sorted({int(_ENTRY_RE.match(label).group(1))
                      for label in by_label if _ENTRY_RE.match(label or "")})
    for index in indexes:
        title = first(f"experience.{index}.title")
        dates = first(f"experience.{index}.dates")
        anchor = f"### {title}" if title else f"### Role {index + 1}"
        if dates:
            anchor += f"\n*{dates}*"
        bullets = [line for line in first(f"experience.{index}.bullets").splitlines()
                   if line]
        entries.append(Entry(anchor=anchor, bullets=bullets))

    name = first("name")
    contact = first("contact")
    preamble = "\n\n".join(part for part in (f"# {name}" if name else "", contact)
                           if part)

    return ParsedResume(preamble=preamble, sections=[
        Section(name=SUMMARY_SECTION, is_tailored=True, body=first("summary"),
                entries=[]),
        Section(name=EXPERIENCE_SECTION, is_tailored=True, body="", entries=entries),
        Section(name=SKILLS_SECTION, is_tailored=True, body=first("skills"),
                entries=[]),
    ])
