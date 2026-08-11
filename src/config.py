from pathlib import Path

ROLE_QUERIES = [
    "software developer",
    "backend developer",
    "fullstack developer",
    "ai engineer",
    "qa automation",
]

# A job needs this fit score (0-100) to earn a tailored CV.
FIT_THRESHOLD = 70

# Sections rewritten per job. Every other section is copied verbatim.
SUMMARY_SECTION = "About Me"
SKILLS_SECTION = "Skills"
EXPERIENCE_SECTION = "Work Experience"
PROJECTS_SECTION = "Projects"
TAILORED_SECTIONS = (SUMMARY_SECTION, SKILLS_SECTION, EXPERIENCE_SECTION, PROJECTS_SECTION)

# The repo root — one level up from src/, where data and output live.
PROJECT_ROOT = Path(__file__).parent.parent
BASE_CV_PATH = PROJECT_ROOT / "base_cv.md"
OUTPUT_DIR = PROJECT_ROOT / "output"

# --- Monid job source ---
MONID_MCP_URL = "https://mcp.monid.ai/v1"
MONID_PROVIDER = "apify"
MONID_ENDPOINT = "/harvestapi/linkedin-job-search"

# Search filters sent to harvestapi (Layer 1 — coarse). Claude scoring is Layer 2.
LOCATION = "Israel"
EXPERIENCE_LEVELS = ["internship", "entry", "associate"]
POSTED_LIMIT = "24h"           # daily window; was "week" during interim tuning
MAX_ITEMS_PER_QUERY = 25       # harvestapi bills per result; maxItems per jobTitle x location

# Keep only postings whose location text contains this (case-insensitive). The
# source's location filter is leaky and returns EMEA/MENA remote roles.
LOCATION_KEYWORD = "israel"

# The CLI truncates oversized MCP tool results to a file BEFORE PostToolUse hooks
# run, which silently defeats the reduction hook. Raised so the full run payload
# reaches the hook, which then shrinks it before the model sees it. A real 24h run
# was 774,006 chars (~193K tokens); a week window is larger.
MAX_MCP_OUTPUT_TOKENS = "500000"

# A SECOND, separate ceiling, and the one that killed the 2026-08-11 live run.
# MAX_MCP_OUTPUT_TOKENS above governs the MCP guard that runs BEFORE the hook.
# What the hook hands BACK is capped at 32,768 bytes by the CLI, with no env var
# to raise it: past that the result is written to tool-results/ and the model gets
# a 2,000-byte preview. Measured directly — 32,800 B and 40,001 B both persisted.
# That run's reduced envelope was 55,198 B (88% of it description text) and the
# agent could see 1 job of 22.
#
# So the reducer no longer serialises descriptions at all: it returns a manifest
# (id/title/company, ~97 B a job) and holds the full postings in process for
# `get_job` to serve one at a time. The two values below are belt and braces — the
# design keeps the envelope near 2KB for a normal run, and these make a pathological
# one fail loudly instead of silently truncating.
INLINE_RESULT_LIMIT_BYTES = 32768        # the measured CLI ceiling; do not raise
SAFE_ENVELOPE_BYTES = 24000              # our own budget, well inside it
MAX_JOB_DESCRIPTION_CHARS = 12000        # so one absurd posting cannot breach it

# --- Canva (Phase R2) ---
# Copies are made from a pinned TEMPLATE, not the live master résumé, so edits to
# the master cannot break a run mid-flight. Re-duplicate the master and re-validate
# the map when the résumé changes; run-start validation surfaces the drift.
CANVA_TEMPLATE_DESIGN_ID = "DAHQxzJVWM4"
CANVA_PAGE_ID = "PB5prZGGYdD17M0v"
CANVA_FOLDER_PREFIX = "Job CVs"          # folder per run: "Job CVs — 2026-07-29"


def _el(suffix):
    return f"{CANVA_PAGE_ID}-{suffix}"


# Slots the pipeline writes, each overwritten wholesale with replace_text.
CANVA_ELEMENT_MAP = {
    "summary": _el("LBrJ8LlFHVgPZm7d"),
    "skills": _el("LBkVtV7y5fKZMm0H"),
    "experience.0.bullets": _el("LBk2rXZgbWWq75bp"),
    "experience.1.bullets": _el("LBzpBGcBgpx9yCWC"),
}

# Never written — mapped only so run-start validation detects layout drift.
CANVA_VALIDATE_ONLY_IDS = [
    _el("LB6dWjhqhy865bfK"), _el("LBm83fB0jYRwNXp0"),   # experience[0] title, date
    _el("LBy14hl84Yxspf65"), _el("LBDfDPSFmCscLJyk"),   # experience[1] title, date
    _el("LBSw3MPln78BRrNQ"), _el("LBBn7RTVpPvK72YS"),   # project[0] title, tech
    _el("LBQSRXttJ86dgQdP"), _el("LBWRLc5NXj6GqzXz"),   # project[1] title, tech
    _el("LBCWJ2xXXDKgHbZT"), _el("LBY9Fc1br0rnxvPL"),   # project[2] title, tech
]

MAX_REDRAFT_ATTEMPTS = 2       # overflow redrafts per job before skipping it
# Cheap prevention only. The authoritative overflow check is the post-edit height
# comparison — skills reordering is length-preserving, so a sub-1.0 ratio would
# reject every run.
LENGTH_BUDGET_RATIO = 1.05
# The summary gets its own, looser budget, derived from the design rather than
# copied from the bullets. Measured on the template: the About Me box sits at
# top 119.31 with the Work Experience header at 229.65, so it has 110.3px of room
# for its current 69.3px — about 4.7 lines against the 3 it uses. Sharing the
# bullets' 1.05 gave a 275-char limit against a 262-char base, i.e. 13 characters
# of headroom to reposition the candidate for a specific role; the first smoke run
# breached it on a perfectly reasonable draft. 1.3 is ~4 lines, still inside the
# box, and the post-edit height check remains the authority.
SUMMARY_LENGTH_BUDGET_RATIO = 1.3
