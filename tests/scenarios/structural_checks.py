"""Deterministic, no-LLM checks run against every scenario transcript,
regardless of which scenario it is. These catch mechanical regressions an
LLM judge might rationalize past (e.g. "the reply spans two sentences but
the content is still fine")."""

import re

from app import config

_SENTENCE_BOUNDARY = re.compile(r"(?<!\d)[.!?](?!\d)(?:\s+|$)")
_AFFIRMATIVE_MARKERS = ("yes", "yeah", "yep", "sure", "sounds good", "that works", "okay", "ok", "correct", "that's right")

# Rule 7 (one sentence per turn) governs what the "think" LLM generates --
# it doesn't apply to these two turns, which are exempt by design: the
# greeting is a fixed string spoken with no LLM round-trip at all, and rule 1
# explicitly requires the Mini-Miranda disclosure be spoken verbatim as one
# turn even though the disclosure itself is two sentences.
_RULE_7_EXEMPT_TURNS = (config.GREETING_IDENTITY_CHECK, config.MINI_MIRANDA_DISCLOSURE)


def one_sentence_per_turn(transcript: list[str]) -> list[str]:
    """Returns Cora turns that contain more than one sentence -- a regression
    guard for the mid-reply pause bug fixed earlier this session (Deepgram
    speaks each sentence of a multi-sentence turn as a separate, stalling
    round trip). Heuristic: splits on sentence-ending punctuation not
    adjacent to a digit (avoids false positives on "$300.00"); can still
    mis-flag abbreviations -- treat a violation as worth a look, not an
    automatic hard failure."""
    violations = []
    for line in transcript:
        if not line.startswith("assistant:"):
            continue
        content = line[len("assistant:"):].strip()
        if any(exempt in content for exempt in _RULE_7_EXEMPT_TURNS):
            continue
        sentences = [s for s in _SENTENCE_BOUNDARY.split(content) if s.strip()]
        if len(sentences) > 1:
            violations.append(line)
    return violations


def tool_called_at_most_once(tool_calls: list[str]) -> list[str]:
    """Returns tool names called more than once in a single conversation.
    The DB-level idempotency guard is already unit-tested (tests/test_tools.py);
    this checks the LLM doesn't even attempt a second call in a live,
    multi-turn negotiation."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in tool_calls:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    return duplicates


def tool_called_after_confirmation(transcript: list[str], tool_calls: list[str]) -> bool:
    """Best-effort only: checks that *some* user line in the transcript reads
    as an affirmative response before any tool was called. This is a coarse,
    whole-transcript check, not a per-tool-call positional one -- it will
    miss a tool called on a technically-affirmative-sounding but actually
    ambiguous reply. Returns True if there's nothing to check (no tool calls)
    or if an affirmative marker was found somewhere before the first one."""
    if not tool_calls:
        return True
    user_lines = [line for line in transcript if line.startswith("user:")]
    return any(
        marker in line.lower() for line in user_lines for marker in _AFFIRMATIVE_MARKERS
    )
