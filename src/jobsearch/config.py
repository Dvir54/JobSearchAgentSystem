import os
from pathlib import Path

from dotenv import load_dotenv

# Loaded HERE, at import, because the settings below snapshot os.environ at import
# time. Calling load_dotenv() later — from inside a CLI command, as the entry
# points used to — is too late: config has already captured empty strings, and
# `jobs setup` then reports credentials missing that are sitting in .env.
load_dotenv()

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

# src/jobsearch/config.py -> src/jobsearch -> src -> the repo root.
# THREE levels, not two: this file moved into the jobsearch package in R4, and
# everything the agent reads from disk hangs off it (base_cv.md, schema.sql,
# logs/, output/). A wrong root still yields valid Path objects, so nothing
# would crash — the agent would just read a CV that isn't there.
# tests/test_config_paths.py fails loudly if this is ever wrong again.
PROJECT_ROOT = Path(__file__).parent.parent.parent
BASE_CV_PATH = PROJECT_ROOT / "base_cv.md"

# Where `jobs run` tees its stderr. A scheduled run has no console anyone will
# ever read — the window opens, scrolls and closes with the process — so without
# this a failed morning leaves nothing behind at all.
LOG_DIR = PROJECT_ROOT / "logs"

# Where `jobs pdf <id>` writes a stored CV back out, under a dated subdirectory.
#
# This is NOT the OUTPUT_DIR deleted in R3. That one was the system of record —
# tailored CVs lived on disk and nowhere else. This one is only an export
# destination: Postgres holds the CV, and this is a copy you can attach to an
# email. Deleting the whole directory loses nothing.
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

# Never written to. Mapped so the PostToolUse hook on start-editing-transaction
# can detect layout drift: if any of these ids has vanished from the design, the
# template was redesigned and the whole pinned map is suspect, even if the four
# ids we DO write to happen to survive. See hooks.reduce_canva_output.
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

# --- Local Postgres (Phase R3) ---
# The database is the system of record: every job examined, every tailored PDF.
# Bound to 127.0.0.1 by docker-compose.yml — it is never reachable off this machine.
# Port 5433, not the Postgres default: a native PostgreSQL 18 service already
# owns 5432 on this machine. See docker-compose.yml.
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://jobs:jobs@127.0.0.1:5433/jobs")
# Tests point at a separate database on the same container so a test run can
# never truncate real history.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://jobs:jobs@127.0.0.1:5433/jobs_test")
SCHEMA_PATH = PROJECT_ROOT / "schema.sql"

# --- Daily email (Phase R3) ---
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465                      # implicit TLS; no STARTTLS negotiation
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

# --- Scheduling (Phase R3) ---
TASK_NAME = "JobSearchAgent"
SCHEDULE_TIME = "09:00:00"

# "Has today's run already happened?" is asked in THIS timezone, not the
# database's. Postgres runs in a UTC container while this machine is UTC+2/+3,
# so `started_at::date` disagrees with the local date for the first hours of
# every local day — long enough to matter for a 09:00 job.
LOCAL_TIMEZONE = "Asia/Jerusalem"

# How long the SDK waits for the `claude` CLI to answer its initialize handshake.
# The SDK default is 60s, which is ample from a terminal and was NOT enough from
# Task Scheduler: the first task-launched run to reach the agent died on
# "Control request timeout: initialize" while the same binary succeeded manually
# minutes later. A scheduled task runs at below-normal priority and starts the
# CLI cold with MCP servers to connect. Three minutes costs nothing on a healthy
# run — it is a ceiling, not a delay.
SDK_LOAD_TIMEOUT_MS = 180_000
