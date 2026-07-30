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
