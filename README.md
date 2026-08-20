# Job Search Agent

> **An AI-powered job search assistant for software developers looking for junior roles in Israel.**

The agent finds relevant job postings, evaluates how well they match the candidate's CV, generates tailored CVs for strong matches, and sends a daily email with the best opportunities.

**It does not apply to jobs automatically.**  
The candidate reviews the results and decides which opportunities to pursue.

---

## ✨ How It Works

```text
┌──────────────┐
│  Job Sources │
└──────┬───────┘
       ↓
┌────────────────────┐
│ Collect & Dedup    │
└────────┬───────────┘
         ↓
┌────────────────────┐
│   AI Fit Analysis  │
└────────┬───────────┘
         ↓
┌────────────────────┐
│   Rank Matches     │
└────────┬───────────┘
         ↓
┌────────────────────┐
│ Generate Tailored  │
│        CV          │
└────────┬───────────┘
         ↓
┌────────────────────┐
│    Daily Email     │
└────────────────────┘
```

For every relevant job, the agent provides:

| | |
|---|---|
| 🎯 **Fit score** | How closely the role matches the candidate |
| 🧠 **AI reasoning** | Why the role is or isn't a good match |
| 🔗 **Apply link** | Direct link to the job posting |
| 📄 **CV ID** | Identifier for the tailored CV |

---

## 📬 Example

A typical daily digest might look like:

```text
4 new job matches

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Software Developer – AI & HR Automation
Check Point Software · Fit 88

Qualifications cap the role at up to two years of
professional experience and focus on web applications,
LLM/agent work, and API integrations.

→ Apply
→ View CV
  CV ID: 4452599968

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

AI Engineer / Full Stack Developer – LLM & RAG
recturicks · Fit 84

The role requires strong Python/React skills and
hands-on LLM API work, which match the candidate's
experience.

→ Apply
→ View CV
  CV ID: 4454746761
```

### 📄 View a Generated CV

Every tailored CV receives a unique ID:

```bash
jobs pdf <cv-id>
```

Example:

```bash
jobs pdf 4452599968
```

---

# 🚀 Quick Start

## Requirements

- **Windows.** The agent registers a Windows scheduled task via `schtasks`, starts
  Docker Desktop from its default install path, and opens exported PDFs with
  `os.startfile`. There is no macOS or Linux path today.
- **Python 3.11+**
- **Docker Desktop**
- **Claude Code**, authorized on this machine. The agent runs on the Claude Agent
  SDK, which drives the `claude` CLI — an API key on its own is not enough.
- **Canva account** with an editable résumé template. Canva is reached through the
  **Canva MCP server**, not an API key: run `claude` once and approve the Canva
  connection before `jobs init`, or setup will stop at step 3.
- **Anthropic API key**
- **Monid API key**
- **Gmail account** with an app password

---

## 1. Install

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it and install the project:

```bash
pip install -e .
```

---

## 2. Configure

Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=...
MONID_API_KEY=...
GMAIL_ADDRESS=...
GMAIL_APP_PASSWORD=...
```

---

## 3. Connect Your CV

Provide the share link to the Canva résumé that will be used as the base template:

```bash
jobs init "<canva-resume-url>"
```

This reads the design, records which text box is which, and writes `base_cv.md`.

**Open `base_cv.md` and check it before going further.** It is the source of truth
for every honesty check the agent makes — which skills it may claim, and how many
bullets each job has. Text pulled out of a design can arrive garbled, so read it
once. `jobs init --force` regenerates it, discarding any edits you have made.

---

## 4. Initialize

```bash
jobs setup
```

This initializes the required services and configuration.

---

## 5. Run

Start a job-search cycle manually:

```bash
jobs run
```

The agent will:

```text
Search
  ↓
Deduplicate
  ↓
Evaluate
  ↓
Rank
  ↓
Generate CVs
  ↓
Send Email
```

---

# 🛠 CLI

| Command | Description |
|---|---|
| `jobs init <canva-url>` | Connect the candidate's base CV |
| `jobs setup` | Initialize the system |
| `jobs run` | Run a job-search cycle |
| `jobs pdf <id>` | View or export a generated CV |

---

# 🏗 Architecture

```text
src/jobsearch/

├── agent/       # Job discovery and AI evaluation
├── resume/      # CV processing and tailoring
├── delivery/    # CLI, email, and scheduling
├── config.py    # Application configuration
└── db.py        # PostgreSQL persistence
```

### Core Components

**🤖 Agent**  
Discovers jobs and evaluates candidate fit.

**📄 Resume Pipeline**  
Creates tailored CVs from the connected Canva template.

**🗄 Database**  
Stores jobs, evaluations, runs, and generated CVs.

**📬 Delivery**  
Handles CLI commands and email notifications.

---

# 🧰 Tech Stack

| Technology | Purpose |
|---|---|
| **Python 3.11+** | Application |
| **Anthropic Claude** | Job evaluation & CV tailoring |
| **Canva MCP** | CV generation & PDF export |
| **PostgreSQL** | Persistence |
| **Docker** | Local database environment |
| **Monid** | Job discovery |
| **Gmail** | Daily digest |
| **Pytest** | Testing |

---

# ⚠️ Limitations

- Applications are **not submitted automatically**.
- CV generation currently targets a **single-page Canva résumé**.
- Generated CVs depend on the structure and editable content of the connected Canva template.
- Each run searches a 24-hour window, so postings older than that are not picked up.
- PostgreSQL is required for persistent job and run history.

---

# 📁 Project Structure

```text
.
├── src/
│   └── jobsearch/
├── tests/
├── docs/
├── docker-compose.yml
├── pyproject.toml
├── schema.sql
└── README.md
```

---

## 📜 License

**MIT**