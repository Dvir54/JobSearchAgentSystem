"""Agent-SDK adapters over tooling.py. Each tool is a thin wrapper; all logic and
enforcement live in tooling.py."""
import json

from claude_agent_sdk import create_sdk_mcp_server, tool

import config
import tooling


def _get_resume_impl():
    return tooling.build_resume_view(config.BASE_CV_PATH.read_text(encoding="utf-8"))


def _prepare_resume_impl(job, score, tailored):
    return tooling.prepare_resume(job, score, tailored)


def _json_result(payload):
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


@tool("get_resume", "Return the base résumé as summary, skills, and indexed Work "
      "Experience / Project entries.", {})
async def get_resume(args: dict) -> dict:
    return _json_result(_get_resume_impl())


_PREPARE_RESUME_SCHEMA = {
    "type": "object",
    "properties": {
        # Identifying fields only. Never the posting's description text — this
        # tool doesn't use it, and re-sending it would round-trip the whole
        # posting as tool arguments once per qualifying job.
        "job": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "company": {"type": "string"},
                "url": {"type": "string"},
            },
            "required": ["id", "title", "company", "url"],
        },
        "score": {"type": "object"},
        "tailored": {"type": "object"},
    },
    "required": ["job", "score", "tailored"],
}


@tool("prepare_resume", "Gate a tailored résumé on relevance, strip invented skills, "
      "repair entry coverage, check the length budget, and return the Canva edit plan "
      "(edits plus ready-to-send operations). Pass `job` as identifying fields only — "
      "id, title, company, url — never the posting's description text.",
      _PREPARE_RESUME_SCHEMA)
async def prepare_resume(args: dict) -> dict:
    return _json_result(_prepare_resume_impl(args["job"], args["score"], args["tailored"]))


@tool("save_pdf", "Download an exported Canva PDF into this run's output directory.",
      {"export_url": str, "company": str, "title": str, "job_id": str})
async def save_pdf(args: dict) -> dict:
    return _json_result(tooling.save_pdf(args["export_url"], args["company"],
                                         args["title"], args["job_id"]))


@tool("write_index", "Write this run's index.md describing every résumé that was created.",
      {"entries": list, "window": str, "skipped_count": int})
async def write_index(args: dict) -> dict:
    return _json_result(tooling.write_index(args["entries"], args["window"],
                                            args["skipped_count"]))


resume_tools = create_sdk_mcp_server(
    name="resume_tools",
    version="1.0.0",
    tools=[get_resume, prepare_resume, save_pdf, write_index],
)
