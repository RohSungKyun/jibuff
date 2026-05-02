"""InterviewEngine — drives structured dialogue until ambiguity threshold is reached.

Orchestrates:
  Stage 1: keyword coverage check (free)
  Stage 2: contradiction detection (LLM)
  Stage 3: dimensional clarity scoring + RiskIndexer (LLM, parallel)
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field

import openai

from orchestrator.config import ModeConfig, get_mode

from .ambiguity import (
    AmbiguityResult,
    DimensionalScore,
    KeywordCoverageResult,
    check_keyword_coverage,
)
from .risk import RiskDimensions, RiskResult

_CHOICE_LINE_RE = re.compile(r"^\s*([abc])[\).:-]\s*(.+)$", flags=re.IGNORECASE)
_CHOICE_INPUT_RE = re.compile(r"^\s*([a-z])\s*[\).:-]?\s*$", flags=re.IGNORECASE)
_DEFAULT_CHOICES = {
    "a": "Use the most common/default assumption",
    "b": "Keep this out of scope for now",
    "c": "Not sure yet; mark it as an open issue",
}

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
- Ask exactly 1 focused clarifying question targeting the missing/weak dimensions above.
- Never suggest solutions or write code.
- Questions must be specific, not generic.
- Provide three short answer choices labeled "a)", "b)", and "c)".
- Include one final line: "직접 입력: <type a custom answer if none fit>"
- Output ONLY this 5-line block, no preamble.
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

_TASK_GEN_PROMPT = """\
Based on the clarified requirements below, generate an atomic task list in Markdown format.

Requirements:
<requirements>{requirements}</requirements>

Rules:
- Each task must be independently completable by an AI coding agent in one session.
- Use this exact format per line: - [ ] P{{phase}}-{{nn}}: {{description}}
- Phase groups: P0 (setup), P1 (core logic), P2 (features/API), P3 (tests & validation)
- Maximum 20 tasks total. Descriptions must be specific and actionable.
- Output ONLY the task list, no headings or preamble.
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


@dataclass(frozen=True)
class QuestionBlock:
    question: str
    choices: dict[str, str]
    custom_label: str = "직접 입력"

    @classmethod
    def from_text(cls, raw: str) -> QuestionBlock:
        """Parse one LLM-produced question block into structured choices."""
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        question_lines: list[str] = []
        choices: dict[str, str] = {}
        custom_label = "직접 입력"

        for line in lines:
            choice_match = _CHOICE_LINE_RE.match(line)
            if choice_match:
                choices[choice_match.group(1).lower()] = choice_match.group(2).strip()
                continue

            if line.startswith("직접 입력"):
                custom_label = line
                continue

            if not choices:
                question_lines.append(line)

        if not question_lines:
            question_lines = [lines[0]] if lines else ["What should be clarified next?"]

        for key, value in _DEFAULT_CHOICES.items():
            choices.setdefault(key, value)

        return cls(
            question=" ".join(question_lines).strip(),
            choices=choices,
            custom_label=custom_label,
        )

    def render(self) -> str:
        lines = [self.question]
        for key in ("a", "b", "c"):
            if key in self.choices:
                lines.append(f"{key}) {self.choices[key]}")
        lines.append(self.custom_label)
        return "\n".join(lines)

    def resolve_answer(self, answer: str) -> str | None:
        """Return normalized answer text, or None when a selection is invalid."""
        cleaned = answer.strip()
        if not cleaned:
            return None

        choice_match = _CHOICE_INPUT_RE.fullmatch(cleaned)
        if choice_match:
            selected = choice_match.group(1).lower()
            if selected in self.choices:
                return f"Selected {selected}: {self.choices[selected]}"
            return None

        return cleaned


@dataclass
class InterviewSession:
    mode: ModeConfig
    original_request: str
    rounds: int = 0
    transcript: list[dict[str, str]] = field(default_factory=list)  # [{role, content}]
    last_ambiguity: AmbiguityResult | None = None
    last_risk: RiskResult | None = None
    pending_question: QuestionBlock | None = None
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
    def __init__(self, mode: str = "quick", client: openai.OpenAI | None = None) -> None:
        self.mode_cfg = get_mode(mode)
        self._client = client or openai.OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        )

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
        if user_answer is not None:
            if not self.validate_user_answer(session, user_answer):
                raise ValueError("Invalid answer. Choose a/b/c or type a custom answer.")
            session.transcript.append({
                "role": "user",
                "content": self._normalize_user_answer(session, user_answer),
            })
            session.pending_question = None

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
        response = self._client.chat.completions.create(
            model="openrouter/auto",
            max_tokens=512,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return (response.choices[0].message.content or "").strip()

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
        block = self._first_question_block(raw)
        if not block:
            session.pending_question = None
            return []
        question = QuestionBlock.from_text(block)
        session.pending_question = question
        return [question.render()]

    def _first_question_block(self, raw: str) -> str:
        """Keep the interview readable by showing one question block per round."""
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if not lines:
            return ""

        block = [lines[0]]
        seen_choice = False
        for line in lines[1:]:
            if _CHOICE_LINE_RE.match(line):
                block.append(line)
                seen_choice = True
                continue
            if line.startswith("직접 입력"):
                block.append(line)
                break
            if not seen_choice:
                break

        return "\n".join(block)

    def _normalize_user_answer(self, session: InterviewSession, answer: str) -> str:
        """Expand a/b/c answers to the selected option text for later scoring."""
        if session.pending_question:
            normalized = session.pending_question.resolve_answer(answer)
            return normalized if normalized is not None else answer.strip()

        return answer.strip()

    def validate_user_answer(self, session: InterviewSession, answer: str) -> bool:
        """Validate selection-like answers while allowing direct typed answers."""
        if not session.pending_question:
            return bool(answer.strip())
        return session.pending_question.resolve_answer(answer) is not None

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

    def generate_tasks_md(self, session: InterviewSession) -> str:
        """Generate a spec/tasks.md from the completed interview session."""
        prompt = _TASK_GEN_PROMPT.format(requirements=session.full_text())
        return self._call(prompt)

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
