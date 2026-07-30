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
        position = container.get("position") or {}
        dimension = container.get("dimension") or {}
        elements[item["element_id"]] = {
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
