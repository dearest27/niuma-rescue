from __future__ import annotations


def route_clarify(output: str) -> tuple[str, str]:
    """Route clarify output by CLEAR/QUESTIONS markers near the top."""
    lines = output.strip().splitlines()
    for i, line in enumerate(lines[:6]):
        marker = line.strip().upper()
        if marker.startswith("CLEAR"):
            return "CLEAR", "\n".join(lines[i + 1:]).strip()
        if marker.startswith("QUESTIONS"):
            return "QUESTIONS", "\n".join(lines[i + 1:]).strip()
    return "QUESTIONS", output.strip()


def review_verdict(output: str) -> str:
    """Return PASS only when review output explicitly says PASS near the top."""
    for line in output.strip().splitlines()[:6]:
        marker = line.strip().upper()
        if marker.startswith("PASS"):
            return "PASS"
        if marker.startswith("FAIL"):
            return "FAIL"
    return "FAIL"
