from pathlib import Path

ROLE_QUERIES = [
    "software engineer",
    "backend developer",
    "fullstack developer",
    "QA automation engineer",
    "DevOps engineer",
    "cybersecurity engineer",
]

# Jobs scraped per role query. The cost control: Apify bills $1.00/1000
# results, so 6 queries x 25 = 150 results = ~$0.15 per run.
# The actor rejects any count below 10 ("must be >= 10").
COUNT_PER_QUERY = 25

# A job needs this fit score (0-100) to earn a tailored CV.
FIT_THRESHOLD = 70

CLAUDE_MODEL = "claude-opus-4-8"
MAX_TOKENS = 16000

ACTOR_ID = "curious_coder/linkedin-jobs-scraper"

# Sections rewritten per job. Every other section is copied verbatim.
SUMMARY_SECTION = "About Me"
SKILLS_SECTION = "Skills"
EXPERIENCE_SECTION = "Work Experience"
PROJECTS_SECTION = "Projects"
TAILORED_SECTIONS = (SUMMARY_SECTION, SKILLS_SECTION, EXPERIENCE_SECTION, PROJECTS_SECTION)

PROJECT_ROOT = Path(__file__).parent
BASE_CV_PATH = PROJECT_ROOT / "base_cv.md"
OUTPUT_DIR = PROJECT_ROOT / "output"
