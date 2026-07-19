"""Assemble the complete output file: a metadata block above a full,
job-tailored resume. Static sections are copied verbatim; tailored sections
are rendered from the model; entry anchors always come from the base CV.
"""
from config import EXPERIENCE_SECTION, PROJECTS_SECTION, SKILLS_SECTION, SUMMARY_SECTION
from resume import ParsedResume
from tailoring import TailoredCV


def _render_entries(tailored_entries, base_entries) -> str:
    blocks: list[str] = []
    for tailored in tailored_entries:
        anchor = base_entries[tailored.entry_index].anchor
        lines = [anchor]
        if tailored.bullets:
            lines.append("")
            lines += [f"- {bullet}" for bullet in tailored.bullets]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _render_resume(parsed: ParsedResume, tailored: TailoredCV) -> str:
    experience = parsed.get(EXPERIENCE_SECTION)
    projects = parsed.get(PROJECTS_SECTION)
    exp_entries = experience.entries if experience else []
    proj_entries = projects.entries if projects else []

    parts = [parsed.preamble]
    for section in parsed.sections:
        if section.name == SUMMARY_SECTION:
            parts.append(f"## {section.name}\n\n{tailored.summary}")
        elif section.name == SKILLS_SECTION:
            parts.append(f"## {section.name}\n\n{', '.join(tailored.skills)}")
        elif section.name == EXPERIENCE_SECTION:
            parts.append(f"## {section.name}\n\n{_render_entries(tailored.experience, exp_entries)}")
        elif section.name == PROJECTS_SECTION:
            parts.append(f"## {section.name}\n\n{_render_entries(tailored.projects, proj_entries)}")
        else:
            parts.append(f"## {section.name}\n\n{section.body}")
    return "\n\n".join(parts)


_MATCH_LABEL = {"direct": "Direct fit", "stretch": "Learnable stretch"}


def render_output(posting, score, parsed: ParsedResume, tailored: TailoredCV) -> str:
    metadata = "\n".join([
        f"- **Fit:** {score.fit_score}/100 — {score.reason}",
        f"- **Match:** {_MATCH_LABEL.get(score.match_kind, score.match_kind)}",
        f"- **Apply at:** {posting.url}",
        "",
        "---",
        "",
    ])
    return metadata + _render_resume(parsed, tailored) + "\n"
