from __future__ import annotations

from pathlib import Path


def review_failure_summary(output: str, report_path: Path, limit: int = 1200) -> str:
    """Build a Feishu/log friendly review failure summary from reviewer output."""
    lines = [line.rstrip() for line in (output or "").strip().splitlines()]
    if lines and lines[0].strip().upper().startswith("FAIL"):
        lines = lines[1:]

    compact: list[str] = []
    blank_seen = False
    for line in lines:
        if not line.strip():
            if blank_seen:
                continue
            blank_seen = True
            compact.append("")
            continue
        blank_seen = False
        compact.append(line)

    body = "\n".join(compact).strip() or "Reviewer 未给出具体原因。"
    if len(body) > limit:
        body = body[:limit].rstrip() + "\n...（已截断，完整内容见 review.md）"
    return f"{body}\n\n完整 Review 报告：{report_path}"
