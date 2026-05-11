"""Helpers for keeping automated QA scoped to machine-checkable work."""

from __future__ import annotations

import re

_TASK_LINE_RE = re.compile(r"^-\s+\[(?P<marker>[ x~!])\]\s+(?P<id>[A-Z0-9]+-\d+):\s+(?P<desc>.+)$")

_VALIDATION_RE = re.compile(
    r"\b(test|verify|validate|confirm|check|qa|acceptance|manual qa)\b"
    r"|테스트|검증|확인|작동|동작",
    re.IGNORECASE,
)
_RUNTIME_ONLY_RE = re.compile(
    r"\b(real|actual|live|production|prod|manual|human|participant|"
    r"participants)\b"
    r"|실제|운영|프로덕션|수동|직접|사람|참가자|참여자",
    re.IGNORECASE,
)
_COUNTED_HUMANS_RE = re.compile(
    r"\b\d+\s*(participants?|users?|customers?|people|humans?)\b"
    r"|\d+\s*명(?:의)?\s*(?:참가자|참여자|사용자|고객|사람)",
    re.IGNORECASE,
)
_AUTOMATED_RE = re.compile(
    r"\b(simulat(?:e|ed|ion)|mock|stub|unit|integration|automated|playwright|"
    r"headless|fake|fixture)\b"
    r"|시뮬레이션|모의|자동화|목|픽스처",
    re.IGNORECASE,
)
_HARD_RUNTIME_RE = re.compile(
    r"\b(real|actual|live|production|prod|manual|human)\b" r"|실제|운영|프로덕션|수동|직접|사람",
    re.IGNORECASE,
)


def requires_runtime_only_validation(text: str) -> bool:
    """Return True when a requirement cannot be resolved by automated QA alone."""
    if not _VALIDATION_RE.search(text):
        return False

    if _AUTOMATED_RE.search(text) and not _HARD_RUNTIME_RE.search(text):
        return False

    return bool(_RUNTIME_ONLY_RE.search(text) or _COUNTED_HUMANS_RE.search(text))


def exclude_runtime_only_validation_tasks(tasks_md: str) -> str:
    """Comment out generated tasks that would block on live/manual validation."""
    sanitized: list[str] = []
    for line in tasks_md.splitlines():
        match = _TASK_LINE_RE.match(line.strip())
        if not match or not requires_runtime_only_validation(match.group("desc")):
            sanitized.append(line)
            continue

        task_id = match.group("id")
        desc = match.group("desc").strip()
        sanitized.append(
            "<!-- Excluded from automated QA "
            f"(requires live/manual verification): {task_id}: {desc} -->"
        )

    return "\n".join(sanitized).strip()
