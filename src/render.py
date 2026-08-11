"""The per-run index. The résumé itself is now a Canva-rendered PDF; this file
carries the operator information that cannot live inside a CV sent to an employer —
fit score, reasoning, the apply URL, and any guard corrections.
"""
from tooling import safe_filename

_MATCH_LABEL = {"direct": "Direct fit", "stretch": "Learnable stretch"}

# Built from the design id, which is permanent, rather than recording the /d/<token>
# URL copy-design hands back: those tokens rotate (the same design returned two
# different ones minutes apart), and every copy carries the template's title, so a
# link that stopped working would leave no way to tell the designs apart.
_CANVA_DESIGN_URL = "https://www.canva.com/design/{}"


def _canva_link(entry):
    design_id = entry.get("canva_design_id")
    if design_id:
        return _CANVA_DESIGN_URL.format(design_id)
    return entry.get("canva_edit_url", "")      # older entries, or id not recorded


def pdf_filename(company, title, job_id):
    """PDF counterpart of safe_filename. Company/title/job_id come from a scraper
    and are never trusted as path components."""
    return safe_filename(company, title, job_id).rsplit(".md", 1)[0] + ".pdf"


def render_index(entries, window, skipped_count):
    lines = [f"# Tailored résumés — {window} window", ""]

    if not entries:
        lines += ["No résumés were written this run.", ""]
    for entry in entries:
        match_kind = entry.get("match_kind", "")
        label = _MATCH_LABEL.get(match_kind, match_kind or "Unknown")
        lines += [
            f"## {entry.get('company', 'Unknown company')} — {entry.get('title', 'Unknown title')}",
            "",
            f"- **Fit:** {entry.get('fit_score', '?')}/100 — {entry.get('reason', '')}",
            f"- **Match:** {label}",
            f"- **Apply at:** {entry.get('apply_url', '')}",
            f"- **PDF:** `{entry.get('pdf_filename', '')}`",
            f"- **Edit in Canva:** {_canva_link(entry)}",
        ]
        if entry.get("corrections"):
            lines.append(
                f"- **⚠️ Auto-corrected:** {'; '.join(entry['corrections'])} — "
                f"review before sending.")
        lines.append("")

    lines += ["---", "", f"{len(entries)} résumé(s) written. "
                         f"{skipped_count} other job(s) judged and skipped.", ""]
    return "\n".join(lines)
