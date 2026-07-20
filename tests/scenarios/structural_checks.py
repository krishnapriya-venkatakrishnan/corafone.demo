"""Deterministic, no-LLM checks run against every scenario transcript,
regardless of which scenario it is. These catch mechanical regressions an
LLM judge might rationalize past (e.g. "the reply spans two sentences but
the content is still fine")."""

import json
import re
from datetime import date

from app import config
from tests.scenarios.harness import TEST_ACCOUNT_BALANCE, TEST_CUSTOMER_NAME

_SENTENCE_BOUNDARY = re.compile(r"(?<!\d)[.!?](?!\d)(?:\s+|$)")
_AFFIRMATIVE_MARKERS = ("yes", "yeah", "yep", "sure", "sounds good", "that works", "okay", "ok", "correct", "that's right")

# Rule 11 (one sentence per turn) governs what the "think" LLM generates --
# it doesn't apply to these two turns, which are exempt by design: the
# greeting is a fixed string spoken with no LLM round-trip at all, and rule 1
# explicitly requires the Mini-Miranda disclosure be spoken verbatim as one
# turn even though the disclosure itself is two sentences.
_SENTENCE_LIMIT_EXEMPT_TURNS = (config.build_greeting(TEST_CUSTOMER_NAME), config.MINI_MIRANDA_DISCLOSURE)

_DOLLAR_RE = re.compile(r"\$[\d,]+(?:\.\d{1,2})?")
_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def one_sentence_per_turn(transcript: list[str]) -> list[str]:
    """Returns Cora turns that contain more than one sentence -- a regression
    guard for the mid-reply pause bug fixed earlier this session (Deepgram
    speaks each sentence of a multi-sentence turn as a separate, stalling
    round trip). Heuristic: splits on sentence-ending punctuation not
    adjacent to a digit (avoids false positives on "$300.00"); can still
    mis-flag abbreviations -- treat a violation as worth a look, not an
    automatic hard failure.

    Rule 11 has two narrow exceptions, both handled explicitly rather than
    left to the digit-adjacency quirk above to paper over by accident:

    1. A turn relaying a counter-offer that states both an amount/schedule
       AND a date may use up to two sentences (splitting one coherent offer
       across turns reads as confusing). Detected heuristically -- a dollar
       figure and a month name both present in the turn -- since this
       function only sees the transcript, not which tool (if any) preceded
       it.
    2. Rule 3's opening anchor -- the turn immediately after the Mini-
       Miranda disclosure -- MUST both state the balance and ask to pay in
       full today, unconditionally two sentences, with no date involved.
       This turn has no month name, so exception 1 never covers it; without
       tracking it explicitly, this turn would only dodge a false flag when
       the balance happens to be a whole dollar amount ending the first
       sentence (the digit-adjacency guard above swallows that particular
       period as a side effect, not by design) -- any other phrasing (a
       trailing word after the figure, a balance with cents whose sentence
       doesn't end right on the digits) loses that accidental cover and
       gets wrongly flagged for doing exactly what rule 3 requires.
       Tracked by position: the turn immediately following one that
       contains the Mini-Miranda disclosure is unconditionally allowed two
       sentences, regardless of content."""
    violations = []
    awaiting_opening_anchor = False
    for line in transcript:
        if not line.startswith("assistant:"):
            continue
        content = line[len("assistant:"):].strip()
        if any(exempt in content for exempt in _SENTENCE_LIMIT_EXEMPT_TURNS):
            awaiting_opening_anchor = config.MINI_MIRANDA_DISCLOSURE in content
            continue
        sentences = [s for s in _SENTENCE_BOUNDARY.split(content) if s.strip()]
        if awaiting_opening_anchor:
            max_allowed = 2
        else:
            max_allowed = 2 if (_DOLLAR_RE.search(content) and any(m in content for m in _MONTH_NAMES)) else 1
        awaiting_opening_anchor = False
        if len(sentences) > max_allowed:
            violations.append(line)
    return violations


# Only tools meant to fire at most once per call, ever. `negotiate` is
# explicitly read-only and designed to be called repeatedly across a
# multi-round negotiation (see its schema description in app/config.py) --
# a real negotiation calling it three or four times across a call is
# normal, expected behavior, not a duplicate-call bug.
_AT_MOST_ONCE_TOOLS = ("record_agreement",)


def tool_called_at_most_once(tool_calls: list[str]) -> list[str]:
    """Returns tool names called more than once in a single conversation,
    restricted to _AT_MOST_ONCE_TOOLS. The DB-level idempotency guard is
    already unit-tested (tests/test_tools.py); this checks the LLM doesn't
    even attempt a second record_agreement call in a live conversation.

    Previously checked every tool name unconditionally, which meant any
    scenario with more than one negotiation round -- i.e. almost every
    real conversation -- flagged the read-only negotiation tool as a
    "duplicate call" purely for being called more than once, which is
    exactly what it's designed for. That produced a false "tool(s) called
    more than once" hard failure on most scenarios, independent of whether
    the agent's actual behavior was correct."""
    seen: set[str] = set()
    duplicates: list[str] = []
    for name in tool_calls:
        if name not in _AT_MOST_ONCE_TOOLS:
            continue
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    return duplicates


def tool_called_after_confirmation(transcript: list[str], tool_calls: list[str]) -> bool:
    """Best-effort only: checks that *some* user line in the transcript reads
    as an affirmative response before `record_agreement` was called.

    Scoped to record_agreement specifically -- `negotiate` is read-only,
    and calling it without any affirmative signal is often correct, not a
    bug: it's the tool used for a deflection, a refusal, or "I don't know"
    just as much as for a genuine proposal -- the opposite of an
    affirmative reply half the time (see rule 3's deflection handling in
    app/config.py). Checking every tool call this way used to flag that
    correct behavior as noise. This is a coarse, whole-transcript
    check, not a per-tool-call positional one -- it will miss a tool called
    on a technically-affirmative-sounding but actually ambiguous reply.
    Returns True if there's nothing to check (record_agreement never
    called) or if an affirmative marker was found somewhere before the
    first tool call."""
    if "record_agreement" not in tool_calls:
        return True
    user_lines = [line for line in transcript if line.startswith("user:")]
    return any(
        marker in line.lower() for line in user_lines for marker in _AFFIRMATIVE_MARKERS
    )


# --- I1: structural (non-LLM) checks against the tool log every scenario's
# transcript now carries (see tests/scenarios/harness.py's `[tool called:
# X]` / `[tool args: ...]` / `[tool result: ...]` lines, mirroring
# app/tools.py's G1 logging). Cheap, deterministic text comparisons -- not
# an LLM judgement -- so they catch the entire class of relay failures in
# the live-testing report's sections A and B automatically: invented
# refusals, dropped floor sentences, reformatted figures, self-initiated
# escalations, offers proposed from memory. ---
_ISO_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ORDINAL_SUFFIXES = ("st", "nd", "rd", "th")


def _iter_tool_events(transcript: list[str]):
    """Yields (tool_name, args, result) for each tool call recorded in the
    transcript, in call order -- parses the `[tool called: X]` / `[tool
    args: ...]` / `[tool result: ...]` line triples harness.py writes."""
    name = args = None
    for line in transcript:
        if line.startswith("[tool called: "):
            name = line[len("[tool called: "):-1]
        elif line.startswith("[tool args: "):
            args = json.loads(line[len("[tool args: "):-1])
        elif line.startswith("[tool result: "):
            if name is not None:
                yield name, args, json.loads(line[len("[tool result: "):-1])
            name = args = None


def _spoken_date_variants(iso: str) -> set[str]:
    """Every way app/negotiation.py's `_speak_date` (and a natural human
    rendering of the same date) might render this ISO date, so a
    machine-formatted tool-log date can still be matched against the
    assistant's spoken form."""
    d = date.fromisoformat(iso)
    month = _MONTH_NAMES[d.month - 1]
    return {f"{month} {d.day}{suffix}" for suffix in ("", *_ORDINAL_SUFFIXES)}


def _balance_figure_variants() -> set[str]:
    """Every reasonable way TEST_ACCOUNT_BALANCE might be written -- with
    or without a thousands comma, with or without trailing cents. This is
    the one dollar figure in the whole call that legitimately has no tool
    behind it: rule 2 hands it to the model as a plain fact in the system
    prompt (never part of the transcript, so it can never appear in the
    tool-call/customer-turn ground truth otherwise), and rule 3's opening
    anchor requires stating it on the very first substantive turn.
    Without this, that mandatory turn would always be flagged as an
    "invented" figure, regardless of what the agent actually did."""
    balance = TEST_ACCOUNT_BALANCE
    variants = {config._speak_dollar_amount(balance), f"${balance:.2f}", f"${balance:,.2f}"}
    if balance == balance.to_integral_value():
        variants.add(f"${int(balance):,}")
    return variants


def assistant_lines_are_grounded(transcript: list[str]) -> list[str]:
    """Every dollar figure and date an assistant turn speaks must appear in
    a tool result/args line from this call, in a customer turn, or be the
    account balance itself (see _balance_figure_variants) -- never
    invented, recalled from memory, or reformatted. Payment counts are not
    checked here (spelled-out numbers can't be matched reliably by a plain
    text comparison); this deliberately covers only what a cheap,
    deterministic scan can check without false positives. The fixed
    greeting and Mini-Miranda lines are exempt -- they're constant strings,
    not tool-derived."""
    exempt_turns = (config.build_greeting(TEST_CUSTOMER_NAME), config.MINI_MIRANDA_DISCLOSURE)

    ground_truth = "\n".join(
        line for line in transcript if line.startswith("user:") or line.startswith("[tool")
    )
    ground_truth += "\n" + " ".join(_balance_figure_variants())
    spoken_dates = set()
    for iso in _ISO_DATE_RE.findall(ground_truth):
        spoken_dates |= _spoken_date_variants(iso)

    violations = []
    for line in transcript:
        if not line.startswith("assistant:"):
            continue
        content = line[len("assistant:"):].strip()
        if any(exempt in content for exempt in exempt_turns):
            continue

        for figure in _DOLLAR_RE.findall(content):
            if figure not in ground_truth:
                violations.append(f"{line}  <-- ungrounded dollar figure {figure!r}")

        for month in _MONTH_NAMES:
            if month not in content:
                continue
            month_dates_in_content = {v for v in spoken_dates if v.startswith(month) and v in content}
            month_mentioned_alone = not any(v in content for v in spoken_dates if v.startswith(month))
            if month_mentioned_alone and not month_dates_in_content:
                violations.append(f"{line}  <-- date mentioning {month!r} not found in any tool result/customer turn")

    return violations


def record_agreement_always_follows_matching_accept(transcript: list[str]) -> list[str]:
    """record_agreement must never succeed without a preceding `negotiate`
    ACCEPT for the SAME terms this call -- otherwise the model could
    hallucinate an approval straight into a write. Compared against the
    ACCEPT's resolved `offer` (from the tool result), not `negotiate`'s
    raw call arguments -- `negotiate` is deliberately called with whatever
    subset of fields the agent actually has (e.g. just `payments`, with
    the total derived internally), so its logged arguments frequently
    don't carry the same keys record_agreement's schema always requires;
    the resolved offer is the one form both sides can always be compared
    on. (app/tools.py's server-side re-validation is defense in depth for
    illegal terms; this checks the model actually followed the intended
    flow, not just that the write was legal.)"""

    def _norm(value):
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return value

    def _terms_match(offer: dict | None, record_args: dict) -> bool:
        if offer is None:
            return False
        dates = offer.get("dates") or []
        return (
            _norm(offer.get("total")) == _norm(record_args.get("total_amount"))
            and _norm(len(offer.get("payments") or [])) == _norm(record_args.get("number_of_payments"))
            and offer.get("cadence") == record_args.get("cadence")
            and (dates[0] if dates else None) == record_args.get("first_payment_date")
        )

    violations = []
    last_accept_offer: dict | None = None
    for name, args, result in _iter_tool_events(transcript):
        if name == "negotiate" and result.get("decision") == "ACCEPT":
            last_accept_offer = result.get("offer")
        elif name == "record_agreement" and result.get("status") == "success":
            if last_accept_offer is None:
                violations.append("record_agreement succeeded with no prior ACCEPT this call")
            elif not _terms_match(last_accept_offer, args or {}):
                violations.append(
                    f"record_agreement succeeded with terms {args} not matching the last ACCEPT offer {last_accept_offer}"
                )
    return violations


_ESCALATION_PHRASE = "pass this to one of our collectors"


def escalation_only_after_no_agreement(transcript: list[str]) -> list[str]:
    """The escalation phrasing (from app/negotiation.py's
    `_no_agreement_verdict`) must never appear in an assistant turn unless
    a tool actually returned NO_AGREEMENT earlier this call -- otherwise
    the agent invented its own conclusion that nothing was possible (see
    the NEVER block in app/config.py's system prompt)."""
    violations = []
    saw_no_agreement = False
    for line in transcript:
        if line.startswith("[tool result: "):
            result = json.loads(line[len("[tool result: "):-1])
            if result.get("decision") == "NO_AGREEMENT":
                saw_no_agreement = True
        if line.startswith("assistant:") and _ESCALATION_PHRASE in line.lower() and not saw_no_agreement:
            violations.append(line)
    return violations


# The most consequential failure mode this project has watched happen live:
# the agent tells the customer their agreement is set when record_agreement
# never actually succeeded this call. Unlike an unauthorized figure --
# which record_agreement's server-side re-validation catches before it can
# persist -- nothing catches this at all once the call has ended. The
# customer hangs up believing there's a payment plan; there is no row, no
# schedule, no obligation on file, and the account never gets called back
# because the system thinks it succeeded.
_SUCCESS_CLAIM_PHRASES = (
    "is due", "you're all set", "you are all set", "is set up", "is now set up",
    "has been recorded", "is on file", "your plan is set", "you're good to go",
    "you are good to go", "we've got that set up", "we have that set up",
)


def success_claimed_without_a_recorded_agreement(transcript: list[str]) -> list[str]:
    """Flags an assistant turn that speaks confirmation-of-completion
    language (rule 6's own examples: "your first payment of $X is due...")
    before `record_agreement` has actually returned status=success this
    call. Order-sensitive: a genuine post-success confirmation (rule 6,
    correctly following a real success) is never flagged, only a claim
    that precedes any real success.

    Heuristic and best-effort, like `escalation_only_after_no_agreement`
    -- phrase-based, so it can both under- and over-fire on natural
    language variation; a legitimate rule 5 confirmation QUESTION ("does
    that work for you?") won't match this phrase list, but a differently-
    worded false claim could still slip past it. Treat a violation as
    demanding a transcript read, not an infallible verdict -- but never
    drop this check for being imperfect. An imperfect detector for this
    failure mode is still better than none, given what it costs when it's
    real."""
    violations = []
    saw_recorded_success = False
    for line in transcript:
        if line.startswith("[tool result: "):
            result = json.loads(line[len("[tool result: "):-1])
            if result.get("status") == "success":
                saw_recorded_success = True
        if line.startswith("assistant:") and not saw_recorded_success:
            content = line[len("assistant:"):].strip().lower()
            if any(phrase in content for phrase in _SUCCESS_CLAIM_PHRASES):
                violations.append(line)
    return violations
