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

from jobsearch.config import (
    EXPERIENCE_SECTION,
    PROJECTS_SECTION,
    SKILLS_SECTION,
    SUMMARY_SECTION,
)
from jobsearch.resume.base_cv import Entry, ParsedResume, Section

# The roles the agent may rewrite.
EDITABLE = ("summary", "skills")
# Labels that carry no index. Entry labels are matched separately.
LABELS = ("summary", "skills", "name", "contact", "heading", "other")
_ENTRY_RE = re.compile(r"^experience\.(\d+)\.(title|dates|bullets)$")
# Projects are never rewritten, but they are what a junior candidate has
# actually built — the agent drafts better when it can see them, and it only
# sees structured entries.
_PROJECT_RE = re.compile(r"^project\.(\d+)\.(title|tech)$")


def reading_order(elements):
    """(element_id, element) pairs, top to bottom then left to right.

    Canva returns elements in creation order, which bears no relation to how the
    page reads. Position is the only reliable ordering.
    """
    return sorted(elements.items(),
                  key=lambda item: (item[1]["top"], item[1]["left"]))


def _is_label(label):
    return (label in LABELS
            or _ENTRY_RE.match(label or "") is not None
            or _PROJECT_RE.match(label or "") is not None)


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




def columns(elements):
    """element_id -> a column key, grouped by transitive horizontal overlap.

    Two-column CVs are the norm, and a heading rarely spans its whole column: a
    measured design had "Education" occupying x 342-548 while the degree dates
    sat at 681-767 — same column, no overlap with the heading's own box. Asking
    which COLUMN a block is in, rather than whether it overlaps a particular
    heading, is what places those correctly.

    Transitive, so a wide paragraph links the narrow boxes on either side of it
    into one column. A block spanning the full page width would merge every
    column into one; the result is then simply less precise, never lossy.
    """
    ids = [element_id for element_id, _ in reading_order(elements)]
    parent = {element_id: element_id for element_id in ids}

    def find(node):
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1:]:
            if _same_column(elements[left_id], elements[right_id]):
                parent[find(left_id)] = find(right_id)
    return {element_id: find(element_id) for element_id in ids}


def _owning_heading(element_id, element, headings, column_of):
    """The heading this block belongs under, or None.

    Among the headings above it in its column, one that also OVERLAPS it
    horizontally wins over one that merely sits higher up the page. That matters
    for a footer row of sections side by side — Volunteering, Military Service,
    Languages — beneath a CV whose body spans the full page width. Those wide
    blocks bridge the three groups into a single column, and "nearest above" then
    resolves to whichever heading is rightmost, collapsing all three sections
    into it.

    The fallback still matters: a heading rarely spans its own column, so a narrow
    date box off to one side overlaps no heading at all and belongs to the last
    one above it.

    None means the block sits above every heading in its column — the header
    block of a CV, whatever the other column is doing at the same height.
    """
    above = [(heading_id, heading) for heading_id, heading in headings
             if heading["top"] <= element["top"]
             and column_of.get(heading_id) == column_of.get(element_id)]
    if not above:
        return None
    overlapping = [heading_id for heading_id, heading in above
                   if _same_column(heading, element)]
    return overlapping[-1] if overlapping else above[-1][0]


def build_resume(labels, elements):
    """A ParsedResume assembled from labelled blocks, ready for render_base_cv.

    LOSSLESS BY CONTRACT: every text block in the design ends up somewhere in the
    result. Labelling decides *where* a block goes and whether it is editable —
    never whether it survives.

    That inversion matters on a CV nobody has seen before. An extractor that keeps
    only what it recognises deletes the rest silently, and a user cannot review
    text that is not there. Here a misjudged block lands in the wrong section
    instead: visible in the file, and fixable by editing markdown.

    Section names are canonical for the three the agent edits, so the parser and
    the guards keep one contract however the design words its headings. Every
    other heading keeps its own name.
    """
    ordered = reading_order(elements)
    headings = [(element_id, element) for element_id, element in ordered
                if labels.get(element_id) == "heading"]
    column_of = columns(elements)
    columns_with_headings = {column_of[heading_id] for heading_id, _ in headings}

    def texts(label):
        return [_text(element) for element_id, element in ordered
                if labels.get(element_id) == label and _text(element)]

    def first(label):
        found = texts(label)
        return found[0] if found else ""

    # --- the three roles the agent edits, plus the entry parts that describe them
    entries = []
    indexes = sorted({int(_ENTRY_RE.match(label).group(1))
                      for label in labels.values() if _ENTRY_RE.match(label or "")})
    for index in indexes:
        title = first(f"experience.{index}.title")
        dates = first(f"experience.{index}.dates")
        anchor = f"### {title}" if title else f"### Role {index + 1}"
        if dates:
            anchor += f"\n*{dates}*"
        bullets = [line for line in first(f"experience.{index}.bullets").splitlines()
                   if line]
        entries.append(Entry(anchor=anchor, bullets=bullets))

    spoken_for = {element_id for element_id, _ in ordered
                  if labels.get(element_id) in ("summary", "skills", "heading")
                  or _ENTRY_RE.match(labels.get(element_id) or "")
                  or _PROJECT_RE.match(labels.get(element_id) or "")}

    # --- everything else is placed, never dropped
    preamble_parts, orphans = [], []
    bodies = {heading_id: [] for heading_id, _ in headings}
    name = first("name")
    if name:
        preamble_parts.append(f"# {name}")

    for element_id, element in ordered:
        if element_id in spoken_for or labels.get(element_id) == "name":
            continue
        text = _text(element)
        if not text:
            continue
        owner = _owning_heading(element_id, element, headings, column_of)
        if owner is not None:
            bodies[owner].append(text)
        elif column_of.get(element_id) in columns_with_headings:
            # Its column has sections and this sits above all of them: the CV's
            # header block — name, title, contact — whatever the other column is
            # doing at the same height.
            preamble_parts.append(text)
        else:
            # A column with no headings at all. Nothing to attribute it to, so it
            # is kept visibly rather than guessed at.
            orphans.append(text)

    project_entries = []
    for index in sorted({int(_PROJECT_RE.match(label).group(1))
                         for label in labels.values()
                         if _PROJECT_RE.match(label or "")}):
        title = first(f"project.{index}.title")
        tech = first(f"project.{index}.tech")
        anchor = f"### {title}" if title else f"### Project {index + 1}"
        if tech:
            anchor += f"\n{tech}"
        project_entries.append(Entry(anchor=anchor, bullets=[]))

    sections = [
        Section(name=SUMMARY_SECTION, is_tailored=True, body=first("summary"),
                entries=[]),
        Section(name=EXPERIENCE_SECTION, is_tailored=True, body="", entries=entries),
        Section(name=SKILLS_SECTION, is_tailored=True, body=first("skills"),
                entries=[]),
    ]
    if project_entries:
        sections.append(Section(name=PROJECTS_SECTION, is_tailored=True, body="",
                                entries=project_entries))
    used = {section.name for section in sections}
    for heading_id, heading in headings:
        heading_name = _text(heading)
        if not heading_name or not bodies[heading_id] or heading_name in used:
            # A heading whose children are all editable roles — "Work Experience" —
            # collects nothing, which is what stops the canonical sections
            # appearing twice.
            orphans.extend(bodies[heading_id] if heading_name in used else [])
            continue
        used.add(heading_name)
        sections.append(Section(name=heading_name, is_tailored=False,
                                body="\n\n".join(bodies[heading_id]), entries=[]))

    if orphans:
        sections.append(Section(name="Additional", is_tailored=False,
                                body="\n\n".join(orphans), entries=[]))
    return ParsedResume(preamble="\n\n".join(preamble_parts), sections=sections)


def coverage_gaps(elements, rendered, labels=None):
    """Element ids whose text does not appear in the rendered CV.

    The assertion behind the lossless contract: no fact about the candidate is
    lost, however the design is laid out. It has to be able to fail, or it proves
    nothing — see the test that feeds it an empty document.

    Section titles are the one deliberate exception, and only when `labels` is
    given. The three sections the agent edits are renamed to canonical headings so
    the parser and the guards keep one contract, which means a design saying
    "Profile" or "Career History" ends up saying "About Me" and "Work Experience".
    That is a transformation, not a leak: every heading either names a section in
    the output or was replaced by the canonical name for it. Headings the agent
    does not recognise keep their own wording.
    """
    missing = []
    for element_id, element in reading_order(elements):
        if labels is not None and labels.get(element_id) == "heading":
            continue
        text = _text(element)
        if not text:
            continue
        if not all(line in rendered for line in text.splitlines()):
            missing.append(element_id)
    return missing
_LABELLING_INSTRUCTIONS = """\
You are labelling the text boxes of one person's CV, taken from a Canva design.

Return ONLY a JSON object mapping element_id to one of these labels:

  summary                 the personal statement / profile paragraph
  skills                  the list of skills, as ONE box
  experience.N.title      job N's title line (N counts from 0, top to bottom)
  experience.N.dates      job N's date line
  experience.N.bullets    job N's bullet points
  project.N.title         project N's name (N counts from 0, top to bottom)
  project.N.tech          the technologies listed under project N
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
- Under a Projects heading, a short name on its own line is `project.N.title`
  and the comma-separated list of technologies beneath it is `project.N.tech`.
- Education, languages, volunteering and military service are `other`: kept in
  the CV for context but never rewritten.
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

