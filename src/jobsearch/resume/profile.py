"""The per-user Canva profile: which text box in their design is which.

This is what makes the agent user-agnostic. Everything about *whose* CV is being
tailored lives in one git-ignored file, so the code itself carries no personal
identity and the same repository works for anyone.

Written by `jobs init`; read by the hooks, the tooling and the workflow prompt.
"""
import json

from jobsearch import config

# The roles the agent may rewrite. Anything else in a design is locked: a slot
# with no guard behind it would be edited with weaker checking than the rest,
# which is the one thing this project exists to prevent.
REQUIRED_SLOTS = ("summary", "skills")
ENTRY_SLOT_PREFIX = "experience."
ENTRY_SLOT_SUFFIX = ".bullets"

_CACHE = None


def reset_cache():
    global _CACHE
    _CACHE = None


def load(path=None):
    """The profile, read once and cached. Raises if `jobs init` has not run."""
    global _CACHE
    if _CACHE is not None and path is None:
        return _CACHE
    target = path or config.PROFILE_PATH
    if not target.is_file():
        raise FileNotFoundError(
            f"No profile at {target}. Run `jobs init` to point the agent at your "
            f"Canva CV.")
    data = json.loads(target.read_text(encoding="utf-8"))
    if path is None:
        _CACHE = data
    return data


def save(data, path=None):
    target = path or config.PROFILE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    reset_cache()


def _is_entry_slot(name):
    return name.startswith(ENTRY_SLOT_PREFIX) and name.endswith(ENTRY_SLOT_SUFFIX)


def problems(data):
    """Everything that would stop a run, as readable lines. Empty means usable."""
    found = []
    slots = data.get("slots") or {}
    for required in REQUIRED_SLOTS:
        if not slots.get(required):
            found.append(f"no {required} block identified")
    if not any(_is_entry_slot(name) for name in slots):
        found.append("no experience entry identified")
    for name in slots:
        if name not in REQUIRED_SLOTS and not _is_entry_slot(name):
            found.append(f"unknown slot {name!r}: the agent has no rule for it")
    for key in ("design_id", "page_id"):
        if not data.get(key):
            found.append(f"missing {key}")
    return found


def slots():
    return load().get("slots") or {}


def locked():
    return load().get("locked") or []


def design_id():
    return load()["design_id"]


def page_id():
    return load()["page_id"]
