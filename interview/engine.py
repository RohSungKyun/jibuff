"""InterviewEngine — drives structured dialogue until ambiguity threshold is reached.

Orchestrates:
  Stage 1: keyword coverage check (free)
  Stage 2: contradiction detection (LLM)
  Stage 3: dimensional clarity scoring + RiskIndexer (LLM, parallel)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import anthropic

from orchestrator.config import ModeConfig, get_mode

from .ambiguity import (
    AmbiguityResult,
    DimensionalScore,
    KeywordCoverageResult,
    check_keyword_coverage,
)
from .risk import RiskDimensions, RiskResult

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_QUESTION_PROMPT = """\
You are a Socratic interviewer helping to clarify software requirements before any code is written.

The developer's request is:
<request>{request}</request>

Current interview transcript:
<transcript>{transcript}</transcript>

Missing or unclear dimensions: {missing}
Weakest clarity dimensions (if scored): {weak_dims}

Your job:
- Ask 1–3 focused clarifying questions targeting the missing/weak dimensions above.
- Never suggest solutions or write code.
- Questions must be specific, not generic.
- Output ONLY the questions, one per line, no preamble.
"""

_CONTRADICTION_PROMPT = """\
You are reviewing software requirements for internal contradictions.

Requirements collected so far:
<requirements>{requirements}</requirements>

List any HIGH-confidence contradictions you find.
Each contradiction must name the two conflicting statements explicitly.
If none found, respond with: NONE

Output format (one per line):
CONFLICT: "<statement A>" contradicts "<statement B>"
"""

_DIMENSIONAL_PROMPT = """\
Score the clarity of these software requirements across 5 dimensions.
Use temperature=0 reasoning.
Each score is a float from 0.0 (completely unclear) to 1.0 (perfectly clear).

Requirements:
<requirements>{requirements}</requirements>

Respond ONLY with valid JSON matching this exact schema:
{{
  "goal": <float>,
  "constraint": <float>,
  "risk": <float>,
  "environment": <float>,
  "success": <float>,
  "reasoning": "<one sentence per dimension, semicolon-separated>"
}}
"""

_RISK_PROMPT = """\
Score the technical risk of these software requirements across 4 dimensions.
Each score is a float from 0.0 (no risk) to 1.0 (critical risk).

Requirements:
<requirements>{requirements}</requirements>

Respond ONLY with valid JSON matching this exact schema:
{{
  "security": <float>,
  "network": <float>,
  "state": <float>,
  "external_api": <float>,
  "justification": "<one sentence summarizing the primary risk>"
}}
"""


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------


@dataclass
class InterviewSession:
    mode: ModeConfig
    original_request: str
    rounds: int = 0
    transcript: list[dict[str, str]] = field(default_factory=list)  # [{role, content}]
    last_ambiguity: AmbiguityResult | None = None
    last_risk: RiskResult | None = None
    complete: bool = False

    def full_text(self) -> str:
        """Concatenate all user-provided content for scoring."""
        parts = [self.original_request]
        for turn in self.transcript:
            if turn["role"] == "user":
                parts.append(turn["content"])
        return "\n".join(parts)

    def transcript_text(self) -> str:
        lines = []
        for turn in self.transcript:
            prefix = "Q" if turn["role"] == "assistant" else "A"
            lines.append(f"{prefix}: {turn['content']}")
        return "\n".join(lines) if lines else "(none yet)"


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class InterviewEngine:
    def __init__(self, mode: str = "quick", client: anthropic.Anthropic | None = None) -> None:
        self.mode_cfg = get_mode(mode)
        self._client = client or anthropic.Anthropic()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, request: str) -> InterviewSession:
        """Create a new interview session."""
        return InterviewSession(mode=self.mode_cfg, original_request=request)

    async def step(self, session: InterviewSession, user_answer: str | None = None) -> list[str]:
        """Advance the interview one step.

        - If user_answer is provided, record it and re-score.
        - Returns a list of follow-up questions (empty if session is complete).
        """
        if user_answer:
            session.transcript.append({"role": "user", "content": user_answer})

        # Stage 1: keyword coverage
        stage1 = check_keyword_coverage(session.full_text(), mode=self.mode_cfg.name)

        if stage1.needs_followup and session.rounds < self.mode_cfg.max_interview_rounds:
            questions = self._generate_questions(session, stage1, weak_dims=[])
            session.transcript.append({"role": "assistant", "content": "\n".join(questions)})
            session.rounds += 1
            return questions

        # Stage 2 + 3 + risk (async parallel)
        contradictions, dim_score, risk_result = await asyncio.gather(
            self._detect_contradictions(session.full_text()),
            self._score_dimensions(session.full_text()),
            self._score_risk(session.full_text()),
        )

        ambiguity = AmbiguityResult.from_stages(
            stage1=stage1,
            contradictions=contradictions,
            dimensions=dim_score,
            mode=self.mode_cfg.name,
            threshold=self.mode_cfg.ambiguity_threshold,
        )
        session.last_ambiguity = ambiguity
        session.last_risk = risk_result

        if ambiguity.passed and risk_result.gate_passed:
            session.complete = True
            return []

        if session.rounds >= self.mode_cfg.max_interview_rounds:
            session.complete = True  # max rounds hit — proceed with open issues
            return []

        weak_dims = dim_score.weakest_dimensions(n=2)
        questions = self._generate_questions(session, stage1, weak_dims=weak_dims)
        session.transcript.append({"role": "assistant", "content": "\n".join(questions)})
        session.rounds += 1
        return questions

    # ------------------------------------------------------------------
    # LLM helpers
    # ------------------------------------------------------------------

    def _call(self, prompt: str) -> str:
        response = self._client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()  # type: ignore[union-attr]

    def _generate_questions(
        self,
        session: InterviewSession,
        stage1: KeywordCoverageResult,
        weak_dims: list[str],
    ) -> list[str]:
        prompt = _QUESTION_PROMPT.format(
            request=session.original_request,
            transcript=session.transcript_text(),
            missing=", ".join(stage1.missing) if stage1.missing else "none",
            weak_dims=", ".join(weak_dims) if weak_dims else "none",
        )
        raw = self._call(prompt)
        return [q.strip() for q in raw.splitlines() if q.strip()]

    async def _detect_contradictions(self, text: str) -> list[str]:
        prompt = _CONTRADICTION_PROMPT.format(requirements=text)
        raw = await asyncio.to_thread(self._call, prompt)
        if raw.strip().upper() == "NONE":
            return []
        return [
            line.removeprefix("CONFLICT:").strip()
            for line in raw.splitlines()
            if "CONFLICT:" in line
        ]

    async def _score_dimensions(self, text: str) -> DimensionalScore:
        prompt = _DIMENSIONAL_PROMPT.format(requirements=text)
        raw = await asyncio.to_thread(self._call, prompt)
        try:
            data = json.loads(raw)
            return DimensionalScore(
                goal=float(data["goal"]),
                constraint=float(data["constraint"]),
                risk=float(data["risk"]),
                environment=float(data["environment"]),
                success=float(data["success"]),
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            # Fallback: treat as fully ambiguous so interview continues
            return DimensionalScore(
                goal=0.0, constraint=0.0, risk=0.0, environment=0.0, success=0.0
            )

    async def _score_risk(self, text: str) -> RiskResult:
        prompt = _RISK_PROMPT.format(requirements=text)
        raw = await asyncio.to_thread(self._call, prompt)
        try:
            data = json.loads(raw)
            dims = RiskDimensions(
                security=float(data["security"]),
                network=float(data["network"]),
                state=float(data["state"]),
                external_api=float(data["external_api"]),
            )
            return RiskResult.from_dimensions(
                dimensions=dims,
                justification=data.get("justification", ""),
                gate=self.mode_cfg.risk_gate,
            )
        except (json.JSONDecodeError, KeyError, ValueError):
            # Fallback: treat as high risk so interview continues
            dims = RiskDimensions(security=1.0, network=1.0, state=1.0, external_api=1.0)
            return RiskResult.from_dimensions(
                dims, justification="scoring failed", gate=self.mode_cfg.risk_gate
            )
