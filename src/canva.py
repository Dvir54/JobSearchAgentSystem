"""Deterministic Canva logic. No claude_agent_sdk import and no network calls —
everything here is a pure function over payloads the agent's MCP calls return, so
it stays unit-testable without an agent run or a Canva account.
"""

_MIN_X_OVERLAP = 20.0     # px of horizontal overlap before two elements share a column
_MIN_Y_GAP = 1.0          # px; guards against float noise when comparing tops


def parse_elements(richtexts):
    """Index a start-editing-transaction `richtexts` array by element_id.

    Only TEXT elements are kept: shapes and image fills are not editable text and
    must not constrain the layout maths.
    """
    elements = {}
    for item in richtexts or []:
        container = item.get("containerElement") or {}
        if container.get("type") != "TEXT":
            continue
        element_id = item.get("element_id")
        if not element_id:
            continue                       # malformed element: nothing to key by
        position = container.get("position") or {}
        dimension = container.get("dimension") or {}
        elements[element_id] = {
            "top": position.get("top", 0.0),
            "left": position.get("left", 0.0),
            "width": dimension.get("width", 0.0),
            "height": dimension.get("height", 0.0),
            "regions": [r.get("text", "") for r in (item.get("regions") or [])],
        }
    return elements


def _x_overlap(a, b):
    return min(a["left"] + a["width"], b["left"] + b["width"]) - max(a["left"], b["left"])


def compute_capacity(elements):
    """Vertical space each element may occupy before colliding with the next one.

    Canva does not reflow: elements are absolutely positioned, so growing text
    overlaps whatever sits below it. Capacity is the distance to the top of the
    nearest element below that shares horizontal space; an element overflows when
    its height exceeds it.
    """
    capacity = {}
    for eid, element in elements.items():
        below = [
            other for oid, other in elements.items()
            if oid != eid
            and other["top"] > element["top"] + _MIN_Y_GAP
            and _x_overlap(element, other) > _MIN_X_OVERLAP
        ]
        capacity[eid] = (min(o["top"] for o in below) - element["top"]) if below else float("inf")
    return capacity


def validate_map(elements, element_map, validate_only_ids):
    """Check the pinned element map still matches the template. Returns problems.

    Run this before any copy or spend. A missing id means the template drifted and
    content would land in the wrong box — abort rather than guess.
    """
    problems = []
    for slot, eid in (element_map or {}).items():
        if eid not in elements:
            problems.append(f"slot {slot!r}: element_id {eid!r} not found in the design")
    for eid in validate_only_ids or []:
        if eid not in elements:
            problems.append(f"validate-only element_id {eid!r} not found in the design")
    return problems


def build_operations(edits, element_map):
    """Turn a slot→edit plan into Canva editing operations.

    Two operation types, because measurement against the real design showed
    replace_text is only safe for some elements:

    - A slot whose value is a string becomes one `replace_text`. That collapses
      the element to a single region which inherits the FIRST original region's
      formatting. For `summary` and `skills` the first region is the real content,
      so bullets/font/colour survive; only the summary's inline bold is lost,
      which we accept.
    - A slot whose value is a list of {"find", "replace"} pairs becomes one
      `find_and_replace_text` per pair. The experience bullet blocks need this:
      their first region is an empty "\\n" spacer paragraph carrying no list
      formatting, so a wholesale replace_text inherits "not a list" and silently
      strips every bullet marker — measured on the live design, not assumed.
      find_and_replace_text rewrites the characters inside each paragraph and
      leaves the paragraph structure (markers, indent, spacer lines) untouched.
    """
    operations = []
    for slot, value in edits.items():
        eid = element_map[slot]             # KeyError on an unknown slot is correct
        if isinstance(value, str):
            operations.append({"type": "replace_text", "element_id": eid, "text": value})
            continue
        for pair in value:
            operations.append({"type": "find_and_replace_text", "element_id": eid,
                               "find_text": pair["find"],
                               "replace_text": pair["replace"]})
    return operations


def _element_text(element):
    return "".join(element.get("regions") or [])


def find_unapplied(operations, elements_after):
    """Operations whose text is NOT present in the element afterwards.

    `edit_operation_results` cannot be trusted on its own: a
    `find_and_replace_text` whose `find_text` matches nothing changes nothing and
    still reports `status: "success"` — measured against the live API. Left
    unchecked that publishes the untailored template bullet as if it were the
    tailored one, which is the worst failure available here (a plausible-looking
    wrong PDF). So we verify against the post-edit text the API itself returns.

    Reports element ids and operation types only; the text stays out, both to keep
    the payload small and because the model does not need it to act.
    """
    unapplied = []
    for operation in operations:
        element = elements_after.get(operation.get("element_id"))
        if element is None:
            continue
        if operation.get("type") == "replace_text":
            wanted = operation.get("text") or ""
        elif operation.get("type") == "find_and_replace_text":
            wanted = operation.get("replace_text") or ""
        else:
            continue                       # not a text write: nothing to verify
        if wanted and wanted not in _element_text(element):
            unapplied.append({"element_id": operation["element_id"],
                              "type": operation["type"]})
    return unapplied


def find_overflows(elements_after, capacity, only_ids):
    """Edited elements whose post-edit height exceeds the space available to them.

    `perform-editing-operations` returns recomputed heights BEFORE commit, so this
    runs on the draft and the transaction can still be cancelled.

    `only_ids` is required, not optional: some elements overlap their neighbours in
    the untouched design (a title box and its bullets, for instance), so checking
    everything would report a false overflow on every job. Only what we wrote is
    our responsibility.
    """
    overflows = {}
    for eid in only_ids:
        element = elements_after.get(eid)
        if element is None:
            continue
        available = capacity.get(eid, float("inf"))
        if element["height"] > available:
            overflows[eid] = {"height": element["height"], "capacity": available,
                              "overflow_px": element["height"] - available}
    return overflows
