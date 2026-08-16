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
import json
import re

from jobsearch.config import EXPERIENCE_SECTION, SKILLS_SECTION, SUMMARY_SECTION
from jobsearch.resume.base_cv import Entry, ParsedResume, Section

# The roles the agent may rewrite.
EDITABLE = ("summary", "skills")
# Labels that carry no index. Entry labels are matched separately.
LABELS = ("summary", "skills", "name", "contact", "heading", "other")
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


# Two boxes belong to the same column when they overlap horizontally by more than
# this. The same threshold canva.py uses to decide whether one box sits above
# another for overflow purposes.
_MIN_X_OVERLAP = 20.0


def _same_column(a, b):
    """True when two boxes overlap horizontally enough to be read as one column.

    CVs are routinely two-column — a sidebar of skills and languages beside a main
    column of experience. Vertical position alone interleaves them, so a sidebar
    heading would otherwise claim the main column's content.
    """
    left = max(a["left"], b["left"])
    right = min(a["left"] + a["width"], b["left"] + b["width"])
    return (right - left) > _MIN_X_OVERLAP


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

    sections = [
        Section(name=SUMMARY_SECTION, is_tailored=True, body=first("summary"),
                entries=[]),
        Section(name=EXPERIENCE_SECTION, is_tailored=True, body="", entries=entries),
        Section(name=SKILLS_SECTION, is_tailored=True, body=first("skills"),
                entries=[]),
    ]
    sections.extend(_extra_sections(labels, elements,
                                    used={s.name for s in sections}))
    return ParsedResume(preamble=preamble, sections=sections)


def _extra_sections(labels, elements, used):
    """Sections the agent never edits, kept because it reads this file when it
    drafts. Dropping a candidate's projects or education would quietly cost them
    context they actually have.

    A `heading` block opens a section; the `other` blocks beneath it are its
    body, in reading order. A heading whose children are all editable roles —
    "Work Experience" — collects nothing and is dropped, which is what keeps the
    canonical sections from appearing twice.
    """
    headings = [(element_id, element)
                for element_id, element in reading_order(elements)
                if labels.get(element_id) == "heading"]
    bodies = {element_id: [] for element_id, _ in headings}

    for element_id, element in reading_order(elements):
        if labels.get(element_id, "other") != "other":
            continue
        owner = None
        for heading_id, heading in headings:
            if heading["top"] <= element["top"] and _same_column(heading, element):
                owner = heading_id          # headings are ordered, so this ends
        if owner is None:                   # up as the nearest one above
            continue
        text = _text(element)
        if text:
            bodies[owner].append(text)

    sections = []
    for heading_id, heading in headings:
        name = _text(heading)
        if not name or not bodies[heading_id] or name in used:
            continue
        used.add(name)
        sections.append(Section(name=name, is_tailored=False,
                                body="\n\n".join(bodies[heading_id]), entries=[]))
    return sections


_LABELLING_INSTRUCTIONS = """\
You are labelling the text boxes of one person's CV, taken from a Canva design.

Return ONLY a JSON object mapping element_id to one of these labels:

  summary                 the personal statement / profile paragraph
  skills                  the list of skills, as ONE box
  experience.N.title      job N's title line (N counts from 0, top to bottom)
  experience.N.dates      job N's date line
  experience.N.bullets    job N's bullet points
  name                    the person's name
  contact                 email, phone, links, location
  heading                 a section title such as "Projects", "Education",
                          "Languages" — the label above a group of blocks
  other                   the content of those sections: project names, degrees,
                          languages, volunteering, military service

Rules:
- Number experience entries from 0, in the order they appear down the page.
- A section title such as "Work Experience" or "Projects" is `heading`, never
  part of an entry.
- The content under those titles is `other`: it is kept in the CV for context
  but never rewritten.
- If unsure, answer `other`. A wrong `other` costs nothing; a wrong editable
  label rewrites the wrong part of someone's CV.

The blocks, in reading order:
"""


def labelling_prompt(elements):
    """What Claude is asked. Position is included because it is evidence: a block
    sitting under a "Work Experience" heading is likely part of an entry however
    it is worded."""
    lines = [_LABELLING_INSTRUCTIONS]
    for element_id, element in reading_order(elements):
        text = _text(element).replace("\n", " / ")
        if len(text) > 300:
            text = text[:300] + "..."
        lines.append(f'- {element_id} (top {element["top"]:.0f}, '
                     f'left {element["left"]:.0f}): {text!r}')
    return "\n".join(lines)


def parse_labelling(reply, elements):
    """element_id -> label, defaulting to 'other'.

    Anything the reply omits, invents or garbles becomes 'other', which is
    locked. The failure mode of this whole step must be "did not tailor" rather
    than "tailored the wrong box".
    """
    labels = {element_id: "other" for element_id in elements}
    start, end = reply.find("{"), reply.rfind("}")
    if start == -1 or end == -1 or end < start:
        return labels
    try:
        proposed = json.loads(reply[start:end + 1])
    except json.JSONDecodeError:
        return labels
    if not isinstance(proposed, dict):
        return labels
    for element_id, label in proposed.items():
        if element_id in labels and isinstance(label, str) and _is_label(label):
            labels[element_id] = label
    return labels


async def label_blocks(elements):
    """Ask Claude what each block is. One call, once per user — not per run."""
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

    reply = []
    options = ClaudeAgentOptions(
        max_turns=1,
        disallowed_tools=["Bash", "Read", "Write", "WebFetch", "Agent"])
    async for message in query(prompt=labelling_prompt(elements), options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    reply.append(block.text)
    return parse_labelling("".join(reply), elements)
