"""QualityEvaluator — ralph cycle implementation.

Scores agent output against the task spec after validators pass.
Only active in modes with quality_threshold set (e.g. rtc).

Dimensions:
  spec_adherence  Does the implementation fully match what was asked?
  code_quality    Is the code clean, readable, and maintainable?
  edge_cases      Are obvious edge cases handled?
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import anthropic

from orchestrator.task_queue import Task

_QUALITY_PROMPT = """\
You are a senior engineer evaluating whether an AI agent's implementation meets a task spec.

Task ID: {task_id}
Task description: {task_description}

Agent output:
<output>
{agent_output}
</output>

Score the implementation on three dimensions (0.0 = completely missing, 1.0 = perfect):
- spec_adherence: Does it fully implement what was described in the task?
- code_quality: Is the code clean, readable, and maintainable?
- edge_cases: Are obvious edge cases and error conditions handled?

Respond ONLY with valid JSON:
{{
  "spec_adherence": <float>,
  "code_quality": <float>,
  "edge_cases": <float>,
  "feedback": "<one sentence identifying the single biggest weakness, or 'looks good' if none>"
}}
"""

_WEIGHTS = {"spec_adherence": 0.5, "code_quality": 0.3, "edge_cases": 0.2}


@dataclass
class QualityResult:
    score: float
    spec_adherence: float
    code_quality: float
    edge_cases: float
    feedback: str
    passed: bool
    threshold: float

    def context(self) -> str:
        """Return a failure summary to inject into the next agent prompt."""
        return (
            f"[Quality gate failed] score={self.score:.2f} < threshold={self.threshold}\n"
            f"  spec_adherence : {self.spec_adherence:.2f}\n"
            f"  code_quality   : {self.code_quality:.2f}\n"
            f"  edge_cases     : {self.edge_cases:.2f}\n"
            f"  feedback       : {self.feedback}\n"
            "Address the feedback above before re-submitting."
        )


@dataclass
class QualityEvaluator:
    threshold: float = 0.7
    client: anthropic.Anthropic | None = None

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = anthropic.Anthropic()

    def evaluate(
        self,
        task: Task,
        agent_output: str,
        workspace: Path,  # reserved for future file-diff analysis
    ) -> QualityResult:
        prompt = _QUALITY_PROMPT.format(
            task_id=task.id,
            task_description=task.description,
            agent_output=agent_output[:4000],  # cap to avoid token overflow
        )

        assert self.client is not None
        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()  # type: ignore[union-attr]

        try:
            data = json.loads(raw)
            spec = float(data["spec_adherence"])
            quality = float(data["code_quality"])
            edge = float(data["edge_cases"])
            feedback = str(data.get("feedback", ""))
        except (json.JSONDecodeError, KeyError, ValueError):
            # Fallback: worst-case scores so the ralph cycle triggers
            spec, quality, edge = 0.0, 0.0, 0.0
            feedback = "Quality evaluation failed to parse — re-run to be safe."

        score = (
            spec * _WEIGHTS["spec_adherence"]
            + quality * _WEIGHTS["code_quality"]
            + edge * _WEIGHTS["edge_cases"]
        )

        return QualityResult(
            score=round(score, 3),
            spec_adherence=spec,
            code_quality=quality,
            edge_cases=edge,
            feedback=feedback,
            passed=score >= self.threshold,
            threshold=self.threshold,
        )
