"""I1: unit tests for the deterministic, no-LLM structural checks against
synthetic transcripts in harness.py's exact line format -- no OpenAI calls,
so these run in the default suite (unlike tests/scenarios/test_scenarios.py,
which is real-LLM and marked `scenario`)."""

from tests.scenarios import structural_checks as sc


def _tool_lines(name: str, args: dict, result: dict) -> list[str]:
    import json

    return [
        f"[tool called: {name}]",
        f"[tool args: {json.dumps(args)}]",
        f"[tool result: {json.dumps(result)}]",
    ]


# --- assistant_lines_are_grounded ---
def test_grounded_dollar_figure_and_date_pass():
    transcript = [
        "assistant: greeting",
        "user: I can only pay $750 today.",
        *_tool_lines(
            "negotiate",
            {"total_amount": 750, "number_of_payments": 1, "cadence": "once", "first_payment_date": "2026-07-19"},
            {"decision": "COUNTER", "reason": "I can offer $750 on July 19th."},
        ),
        "assistant: I can offer $750 on July 19th.",
    ]
    assert sc.assistant_lines_are_grounded(transcript) == []


def test_invented_dollar_figure_flagged():
    transcript = [
        *_tool_lines(
            "negotiate",
            {"total_amount": 800, "number_of_payments": 1, "cadence": "once", "first_payment_date": "2026-07-19"},
            {"decision": "COUNTER", "reason": "I can offer $800 on July 19th."},
        ),
        "assistant: I can offer $900 on July 19th.",  # $900 never appeared anywhere
    ]
    violations = sc.assistant_lines_are_grounded(transcript)
    assert len(violations) == 1
    assert "$900" in violations[0]


def test_customer_stated_figure_is_grounded():
    transcript = [
        "user: Can you do $850?",
        "assistant: Let me check -- I can offer $850 today.",
    ]
    assert sc.assistant_lines_are_grounded(transcript) == []


def test_fixed_greeting_and_mini_miranda_are_exempt():
    from app import config
    from tests.scenarios.harness import TEST_CUSTOMER_NAME

    transcript = [
        f"assistant: {config.build_greeting(TEST_CUSTOMER_NAME)}",
        f"assistant: {config.MINI_MIRANDA_DISCLOSURE}",
    ]
    assert sc.assistant_lines_are_grounded(transcript) == []


def test_opening_anchor_stating_the_balance_is_grounded():
    """Regression: the account balance is a plain fact handed to the model
    in the system prompt (rule 2), never part of the transcript itself, so
    it can never appear in a tool result or a customer turn -- yet rule 3's
    opening anchor REQUIRES stating it on the very first substantive turn.
    Without _balance_figure_variants, that mandatory turn was always
    flagged as an invented figure, regardless of what the agent did."""
    from app import config
    from tests.scenarios.harness import TEST_ACCOUNT_BALANCE

    balance_spoken = config._speak_dollar_amount(TEST_ACCOUNT_BALANCE)
    transcript = [
        f"assistant: The balance on your account is {balance_spoken}. "
        "Are you able to take care of that in full today?",
    ]
    assert sc.assistant_lines_are_grounded(transcript) == []


# --- record_agreement_always_follows_matching_accept: matched against the
# ACCEPT's resolved `offer` (from the tool result), not `negotiate`'s raw
# call arguments -- negotiate is deliberately called with whatever subset
# of fields the agent has, so its logged args don't always carry the same
# keys record_agreement's schema requires; the offer is the one form both
# sides can be compared on. ---
_FULL_PAYMENT_OFFER = {
    "tier": "full_payment", "total": "500.00", "payments": ["500.00"],
    "dates": ["2026-07-19"], "cadence": "once",
}


def test_record_agreement_after_matching_accept_passes():
    transcript = [
        *_tool_lines(
            "negotiate",
            {"total_amount": 500, "number_of_payments": 1, "cadence": "once", "first_payment_date": "2026-07-19"},
            {"decision": "ACCEPT", "reason": "That works.", "offer": _FULL_PAYMENT_OFFER},
        ),
        *_tool_lines(
            "record_agreement",
            {
                "tier": "full_payment", "total_amount": 500, "number_of_payments": 1,
                "cadence": "once", "first_payment_date": "2026-07-19",
            },
            {"status": "success", "tier": "full_payment"},
        ),
    ]
    assert sc.record_agreement_always_follows_matching_accept(transcript) == []


def test_record_agreement_after_matching_accept_passes_with_payments_only_call():
    """The ACCEPT came from a `negotiate(payments=[...])` call with no
    total_amount in its raw args at all (the total was derived internally
    -- see negotiate()'s resolution order) -- matching must still succeed
    because it's keyed on the resolved `offer`, not those raw args."""
    transcript = [
        *_tool_lines(
            "negotiate",
            {"payments": [500]},
            {"decision": "ACCEPT", "reason": "That works.", "offer": _FULL_PAYMENT_OFFER},
        ),
        *_tool_lines(
            "record_agreement",
            {
                "tier": "full_payment", "total_amount": 500, "number_of_payments": 1,
                "cadence": "once", "first_payment_date": "2026-07-19",
            },
            {"status": "success", "tier": "full_payment"},
        ),
    ]
    assert sc.record_agreement_always_follows_matching_accept(transcript) == []


def test_record_agreement_with_no_prior_accept_flagged():
    transcript = [
        *_tool_lines(
            "record_agreement",
            {
                "tier": "full_payment", "total_amount": 500, "number_of_payments": 1,
                "cadence": "once", "first_payment_date": "2026-07-19",
            },
            {"status": "success", "tier": "full_payment"},
        ),
    ]
    violations = sc.record_agreement_always_follows_matching_accept(transcript)
    assert len(violations) == 1
    assert "no prior ACCEPT" in violations[0]


def test_record_agreement_with_mismatched_terms_flagged():
    transcript = [
        *_tool_lines(
            "negotiate",
            {"total_amount": 500, "number_of_payments": 1, "cadence": "once", "first_payment_date": "2026-07-19"},
            {"decision": "ACCEPT", "reason": "That works.", "offer": _FULL_PAYMENT_OFFER},
        ),
        *_tool_lines(
            "record_agreement",
            {
                "tier": "full_payment", "total_amount": 800, "number_of_payments": 1,
                "cadence": "once", "first_payment_date": "2026-07-19",
            },
            {"status": "success", "tier": "full_payment"},
        ),
    ]
    violations = sc.record_agreement_always_follows_matching_accept(transcript)
    assert len(violations) == 1
    assert "not matching" in violations[0]


def test_record_agreement_rejected_status_never_flagged():
    """Only a *successful* record_agreement needs a matching prior ACCEPT --
    app/tools.py's own server-side re-validation already refuses anything
    else, so a "rejected"/"already_recorded" status is not this check's
    concern."""
    transcript = [
        *_tool_lines(
            "record_agreement",
            {
                "tier": "settlement", "total_amount": 100, "number_of_payments": 1,
                "cadence": "once", "first_payment_date": "2026-07-19",
            },
            {"status": "rejected", "reason": "..."},
        ),
    ]
    assert sc.record_agreement_always_follows_matching_accept(transcript) == []


# --- escalation_only_after_no_agreement ---
def test_escalation_after_no_agreement_passes():
    transcript = [
        *_tool_lines(
            "negotiate",
            {"total_amount": 50, "number_of_payments": 1, "cadence": "once", "first_payment_date": "2026-07-19"},
            {"decision": "NO_AGREEMENT", "reason": "I'm not able to put together an arrangement..."},
        ),
        "assistant: I'll pass this to one of our collectors to look at.",
    ]
    assert sc.escalation_only_after_no_agreement(transcript) == []


def test_escalation_without_no_agreement_flagged():
    transcript = [
        *_tool_lines(
            "negotiate",
            {"total_amount": 400, "number_of_payments": 2, "cadence": "monthly", "first_payment_date": "2026-07-19"},
            {"decision": "COUNTER", "reason": "I can offer $400 x 2."},
        ),
        "assistant: I'll pass this to one of our collectors to look at.",
    ]
    violations = sc.escalation_only_after_no_agreement(transcript)
    assert len(violations) == 1


# --- tool_called_at_most_once: only record_agreement is meant to fire at
# most once per call. `negotiate` is read-only and explicitly designed to
# be called repeatedly across a multi-round negotiation -- calling it
# several times in one conversation must never be flagged. ---
def test_repeated_negotiate_calls_never_flagged():
    """A real multi-round negotiation calls this several times -- that's
    the whole point of it being read-only and re-callable, not a bug.
    RE-BASELINED: this test used to be two (one named
    validate_consumer_proposal, one named request_next_offer, testing the
    identical assertion under two now-nonexistent tool names) -- merged
    into one now that there's only one read-only tool."""
    tool_calls = ["negotiate"] * 4
    assert sc.tool_called_at_most_once(tool_calls) == []


def test_repeated_record_agreement_calls_flagged():
    tool_calls = ["negotiate", "record_agreement", "record_agreement"]
    assert sc.tool_called_at_most_once(tool_calls) == ["record_agreement"]


def test_single_record_agreement_call_never_flagged():
    tool_calls = ["negotiate", "negotiate", "record_agreement"]
    assert sc.tool_called_at_most_once(tool_calls) == []


# --- one_sentence_per_turn: rule 11 caps every turn at one sentence, with
# one narrow exception -- a turn relaying a counter-offer that states both
# an amount AND a date may use up to two. ---
def test_single_sentence_turn_passes():
    transcript = ["assistant: I can offer $800 today."]
    assert sc.one_sentence_per_turn(transcript) == []


def test_plain_two_sentence_turn_flagged():
    transcript = ["assistant: I understand. Let me help you with that."]
    violations = sc.one_sentence_per_turn(transcript)
    assert len(violations) == 1


def test_two_sentence_counter_offer_with_amount_and_date_exempt():
    """Rule 11's exception: an amount AND a date together earn a second
    sentence -- e.g. relaying a two-payment counter-offer."""
    transcript = [
        "assistant: I can't approve that, but I can offer $750 on July 19th. "
        "The second payment of $250 is due August 2nd.",
    ]
    assert sc.one_sentence_per_turn(transcript) == []


def test_three_sentence_turn_with_amount_and_date_still_flagged():
    """The exception caps at two sentences, not unlimited."""
    transcript = [
        "assistant: I can't approve that. But I can offer $750 on July 19th. "
        "The rest is due August 2nd.",
    ]
    violations = sc.one_sentence_per_turn(transcript)
    assert len(violations) == 1


def test_two_sentence_turn_with_only_an_amount_still_flagged():
    """A dollar figure alone (no date) doesn't earn the exception -- both
    must be present."""
    transcript = ["assistant: I can offer $800 today. Does that work for you?"]
    violations = sc.one_sentence_per_turn(transcript)
    assert len(violations) == 1


def test_greeting_and_mini_miranda_exempt_regardless_of_sentence_count():
    from app import config
    from tests.scenarios.harness import TEST_CUSTOMER_NAME

    transcript = [
        f"assistant: {config.build_greeting(TEST_CUSTOMER_NAME)}",
        f"assistant: {config.MINI_MIRANDA_DISCLOSURE}",
    ]
    assert sc.one_sentence_per_turn(transcript) == []


# --- The opening anchor (rule 3): the turn immediately after the Mini-
# Miranda disclosure must state the balance AND ask to pay in full today,
# unconditionally two sentences, no date involved. Tracked by position
# (the turn right after Mini-Miranda), not by content pattern -- the
# amount+date exception doesn't cover it (no date here), and relying on
# the digit-adjacency quirk to paper over it by accident breaks the
# moment the sentence doesn't happen to end right on the dollar figure's
# digits (see the two "previously broke the accident" cases below). ---
def test_opening_anchor_two_sentences_passes_when_digit_adjacent():
    """The one phrasing shape that happened to dodge a flag by accident
    (the sentence-ending period lands right after the balance's digits) --
    must still pass now that it's an explicit, position-tracked exemption
    rather than a side effect."""
    from app import config

    transcript = [
        f"assistant: {config.MINI_MIRANDA_DISCLOSURE}",
        "assistant: The balance on your account is $1000. Are you able to take care of that in full today?",
    ]
    assert sc.one_sentence_per_turn(transcript) == []


def test_opening_anchor_two_sentences_passes_with_non_digit_adjacent_phrasing():
    """The accidental digit-adjacency cover disappears the moment a word
    follows the dollar figure before the period -- this phrasing used to
    be wrongly flagged for doing exactly what rule 3 requires."""
    from app import config

    transcript = [
        f"assistant: {config.MINI_MIRANDA_DISCLOSURE}",
        "assistant: The balance on your account is $1000 for your account. Are you able to take care of that in full today?",
    ]
    assert sc.one_sentence_per_turn(transcript) == []


def test_opening_anchor_two_sentences_passes_with_cents_and_awkward_phrasing():
    from app import config

    transcript = [
        f"assistant: {config.MINI_MIRANDA_DISCLOSURE}",
        "assistant: Your account balance is $999.50 as of today. Are you able to pay that in full?",
    ]
    assert sc.one_sentence_per_turn(transcript) == []


def test_opening_anchor_exemption_does_not_leak_into_later_turns():
    """Only the ONE turn immediately after Mini-Miranda is exempt -- a
    later, genuinely-bad multi-sentence turn elsewhere in the same call
    must still be caught."""
    from app import config

    transcript = [
        f"assistant: {config.MINI_MIRANDA_DISCLOSURE}",
        "assistant: The balance on your account is $1000. Are you able to take care of that in full today?",
        "assistant: I understand. Let me help. One more thing.",
    ]
    violations = sc.one_sentence_per_turn(transcript)
    assert violations == ["assistant: I understand. Let me help. One more thing."]


def test_two_sentences_without_mini_miranda_preceding_still_flagged():
    """The position-based exemption only fires immediately after Mini-
    Miranda -- an identically-shaped two-sentence turn appearing anywhere
    else (no Mini-Miranda turn right before it) is not the opening anchor
    and must still be flagged. Uses non-digit-adjacent phrasing so the
    unrelated digit-adjacency quirk (see the "accident" tests above)
    doesn't mask what's actually being tested here: position, not
    content."""
    transcript = [
        "assistant: The balance on your account is $1000 for your account. Are you able to take care of that in full today?"
    ]
    violations = sc.one_sentence_per_turn(transcript)
    assert len(violations) == 1


# --- tool_called_after_confirmation: scoped to record_agreement only --
# `negotiate` is read-only and is often correctly called with no
# affirmative signal at all (a deflection or refusal is precisely what it
# exists for, just as much as a genuine proposal). ---
def test_negotiate_alone_never_requires_confirmation():
    """RE-BASELINED: this test used to be two (request_next_offer alone,
    validate_consumer_proposal alone) testing the identical assertion
    under two now-nonexistent tool names -- merged into one now that
    there's only one read-only tool, called here on a non-affirmative
    reply, exactly the case it's designed for."""
    transcript = ["user: I don't know, not right now."]
    tool_calls = ["negotiate"]
    assert sc.tool_called_after_confirmation(transcript, tool_calls) is True


def test_record_agreement_with_prior_affirmative_passes():
    transcript = ["user: Yes, that works."]
    tool_calls = ["negotiate", "record_agreement"]
    assert sc.tool_called_after_confirmation(transcript, tool_calls) is True


def test_record_agreement_with_no_affirmative_anywhere_flagged():
    transcript = ["user: I don't know."]
    tool_calls = ["negotiate", "record_agreement"]
    assert sc.tool_called_after_confirmation(transcript, tool_calls) is False


# --- success_claimed_without_a_recorded_agreement: the most severe
# failure mode -- the agent tells the customer their agreement is set
# with no successful record_agreement behind it. ---
def test_success_claim_with_no_record_agreement_call_at_all_flagged():
    transcript = [
        "user: Yes, that works.",
        "assistant: Great, your first payment of $500 is due July 20th.",
    ]
    violations = sc.success_claimed_without_a_recorded_agreement(transcript)
    assert len(violations) == 1


def test_success_claim_after_genuine_success_not_flagged():
    """Rule 6's actual, correct flow: the confirmation-of-completion
    language only follows a real record_agreement success."""
    transcript = [
        *_tool_lines(
            "record_agreement",
            {"tier": "full_payment", "total_amount": 500, "number_of_payments": 1,
             "cadence": "once", "first_payment_date": "2026-07-19"},
            {"status": "success", "tier": "full_payment"},
        ),
        "assistant: Great, your first payment of $500 is due July 20th.",
    ]
    assert sc.success_claimed_without_a_recorded_agreement(transcript) == []


def test_success_claim_before_a_later_real_success_still_flagged():
    """Order-sensitive: claiming completion BEFORE the real success --
    even if record_agreement genuinely succeeds later in the same call --
    is still the false claim, at the moment it was spoken."""
    transcript = [
        "assistant: Great, your first payment of $500 is due July 20th.",
        *_tool_lines(
            "record_agreement",
            {"tier": "full_payment", "total_amount": 500, "number_of_payments": 1,
             "cadence": "once", "first_payment_date": "2026-07-19"},
            {"status": "success", "tier": "full_payment"},
        ),
    ]
    violations = sc.success_claimed_without_a_recorded_agreement(transcript)
    assert len(violations) == 1


def test_confirmation_question_not_flagged_as_a_success_claim():
    """Rule 5's confirmation turn (a question, asked BEFORE recording) must
    not be confused with a claim that recording already happened."""
    transcript = [
        "assistant: So to confirm, that's $500 paid in full today -- does that work for you?",
    ]
    assert sc.success_claimed_without_a_recorded_agreement(transcript) == []


def test_rejected_record_agreement_does_not_suppress_a_later_false_claim():
    """A REJECTED record_agreement call must not count as a real success --
    a claim following it is still false."""
    transcript = [
        *_tool_lines(
            "record_agreement",
            {"tier": "full_payment", "total_amount": 100, "number_of_payments": 1,
             "cadence": "once", "first_payment_date": "2026-07-19"},
            {"status": "rejected", "reason": "..."},
        ),
        "assistant: Great, you're all set -- thanks for your time!",
    ]
    violations = sc.success_claimed_without_a_recorded_agreement(transcript)
    assert len(violations) == 1
