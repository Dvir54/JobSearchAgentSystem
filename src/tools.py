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


@tool("prepare_resume", "Gate a tailored résumé on relevance, strip invented skills, "
      "repair entry coverage, check the length budget, and return the Canva edit plan.",
      {"job": dict, "score": dict, "tailored": dict})
async def prepare_resume(args: dict) -> dict:
    return _json_result(_prepare_resume_impl(args["job"], args["score"], args["tailored"]))


resume_tools = create_sdk_mcp_server(
    name="resume_tools",
    version="1.0.0",
    tools=[get_resume, prepare_resume],
)
