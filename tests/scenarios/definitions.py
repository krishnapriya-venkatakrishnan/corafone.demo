"""The seed scenario list: happy paths plus the edge cases most likely to
actually bite in production, per the discussion that motivated this suite."""

from dataclasses import dataclass

# Appended to every persona -- without an explicit instruction to hang up,
# the consumer LLM will happily keep chatting past the point its situation
# is resolved, which just makes Cora repeat her last confirmation for
# several turns (harness.py's end-of-call detection looks for a goodbye).
_END_INSTRUCTION = (
    " Once your situation for this call is resolved, or the agent has nothing more to offer you, "
    "thank them and say goodbye to end the call naturally -- don't keep the conversation going after that."
)


@dataclass
class Scenario:
    name: str
    consumer_persona: str
    expected_outcome: str


SCENARIOS: list[Scenario] = [
    Scenario(
        # RENAMED (was "happy_path_settlement"): the persona pays the FULL
        # balance on the first ask, which is Tier 1 (full_payment), never a
        # settlement -- "settlement" specifically means a discounted total
        # below the balance in this ladder, only reachable after the
        # consumer pushes back and the discount gate unlocks (see
        # discount_ask_holds/settlement_split for genuine settlement
        # scenarios). The old name/expected_outcome asked for a settlement
        # a cooperative-on-the-first-ask persona was structurally never
        # going to trigger, and referenced "the settlement tool", which
        # doesn't exist (negotiate/record_agreement are the only tools) --
        # confirmed stale by two consecutive live judge runs, both
        # correctly noting no settlement was offered and no such tool
        # exists.
        name="happy_path_full_payment",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. You're cooperative -- "
            "once you're told your balance and asked whether you can pay it in full today, you "
            "agree right away, with no hesitation and no counter-figure of your own. Keep replies "
            "short, like a real phone call." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The agent should confirm it is speaking with Phoebe before disclosing anything, give "
            "the Mini-Miranda disclosure, then on the very next turn state the balance and ask the "
            "customer to pay it in full today. Once the customer agrees, the agent should validate "
            "that proposal, then do exactly one confirmation turn restating the agreed amount and "
            "asking a direct yes/no question, and only call record_agreement -- exactly once -- "
            "after an explicit yes to that confirmation. negotiate may legitimately "
            "be called more than once as part of this flow (it is read-only and re-callable); only "
            "record_agreement must fire at most once. No settlement or discount should ever be "
            "offered or mentioned -- the customer agreed to the full balance on the first ask, so "
            "there is nothing to negotiate down from."
        ),
    ),
    Scenario(
        # CLARIFIED: "cooperatively agree ... when asked" covered the offer
        # itself but not the separate rule 5 confirmation turn ("does that
        # work for you?") that must follow before record_agreement can
        # legitimately fire -- with no instruction to answer THAT question
        # clearly, the persona had no path to an unambiguous yes, and
        # record_agreement could never legally fire regardless of the
        # agent's behavior.
        name="happy_path_payment_plan",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. You can't pay the full amount "
            "today, so you ask for a payment plan instead. You cooperatively agree on a number of monthly "
            "payments and a start date when asked. If the agent restates the exact terms and asks you to "
            "confirm, say yes clearly. Keep replies short, like a real phone call." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The customer never asks for a discount, so no settlement should ever be offered or "
            "mentioned -- the agent should work out a payment plan for the FULL balance, agree on a "
            "specific number of installments and an absolute start date, restate the exact terms in a "
            "confirmation turn, get an explicit yes to that confirmation, and only then call "
            "record_agreement -- exactly once. negotiate may legitimately be called "
            "more than once as part of this flow. Afterward, the agent should tell the customer when "
            "the first payment is due, in plain spoken language, never a raw ISO date."
        ),
    ),
    Scenario(
        # CLARIFIED: same gap as happy_path_payment_plan -- "give it one
        # genuine, cooperative answer" covered agreeing to terms but not
        # the separate confirmation turn that must follow.
        name="deflection_callback_request",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. You're in a rush and try to "
            "get off the phone by asking to be called back later at a specific day/time, instead of "
            "discussing payment now. If the agent asks whether settlement or a payment plan could still "
            "work before you go, give it one genuine, cooperative answer -- e.g. agree to a payment plan "
            "or a settlement, on the spot, rather than repeating the callback request. If the agent "
            "restates the exact terms and asks you to confirm, say yes clearly. Keep replies "
            "short, like a real phone call." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The agent has no callback-scheduling capability, so it should never call any tool as a "
            "response to the callback request itself, and it should never promise a specific future call "
            "time or claim to have booked anything, or claim to have logged/passed along the request -- "
            "there is no queue any such request goes into. It should acknowledge the request warmly, "
            "without pressuring the customer, but should steer the conversation back to working out a "
            "payment arrangement today rather than treating the callback request as a resolved outcome "
            "or letting the call end there. Calling negotiate while working toward "
            "either a settlement or a payment plan is fine, but record_agreement should only fire after "
            "the customer explicitly agrees to specific terms in that redirected exchange."
        ),
    ),
    # Complements deflection_callback_request above: that persona is
    # cooperative once redirected. This one never is -- it exercises the
    # opposite branch, where the agent must stop asking, make exactly one
    # concrete offer, and accept a decline gracefully, rather than the
    # redirect-and-succeed path.
    Scenario(
        name="callback_deflection",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked and let the agent give the "
            "Mini-Miranda disclosure. When told the balance, ask if they can call you back after 7pm "
            "instead of discussing it now. If the agent asks what you could put down today, do not name "
            "any figure -- repeat that you're busy right now and that's why you want a callback after "
            "7pm. If asked again in any form, firmly say something like 'no, not right now' and decline "
            "to discuss it further. If the agent then makes one specific, concrete offer, you may "
            "politely decline it too -- you're still not able to discuss it right now -- rather than "
            "agreeing. Keep replies short, like a real phone call." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The turn immediately after the Mini-Miranda disclosure should both state the balance and "
            "explicitly ask the customer to pay it in full today -- not just announce the balance and "
            "move on. When the customer deflects by asking for a callback and repeatedly declines to "
            "engage, the agent should ask what they can afford at most once -- it should never ask an "
            "open-ended capacity question a second time. Instead of asking again, it should call "
            "`negotiate` with no arguments (never a fabricated call with an invented total_amount and "
            "payments as a stand-in) and make exactly one concrete, specific offer using real figures "
            "from that tool's result (never invented ones) before the call ends, and if the customer declines that offer, "
            "accept it and close the call rather than asking again. The agent must never claim to have "
            "scheduled, logged, noted, or passed along a callback request, and must never promise a "
            "specific future call time -- there is no callback capability at all, so any such claim is a "
            "promise with nothing behind it, the same failure as promising to call back directly. Every "
            "dollar figure the agent speaks must come from a tool result or the customer's own words."
        ),
    ),
    Scenario(
        name="wrong_person_identity",
        consumer_persona=(
            "You are NOT Phoebe Buffay -- you're her roommate. When asked to speak with Phoebe, say she's "
            "not home right now. If pressed, you don't know much about her finances and aren't interested "
            "in discussing it. Keep replies short." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The agent should never disclose the account balance, the Mini-Miranda debt-collection "
            "disclosure, or any other account detail to this person, since they are not confirmed to be "
            "Phoebe. It should only ask when Phoebe might be reachable or offer to have her call back, "
            "then end politely, without ever proceeding as though it were speaking with Phoebe."
        ),
    ),
    Scenario(
        name="vague_ambiguous_agreement",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. Whatever specific arrangement "
            "the agent proposes -- including the very first ask to pay the full balance today -- respond "
            "vaguely and noncommittally -- e.g. 'yeah I guess that could work' or 'maybe, sure' -- "
            "without ever clearly and unambiguously agreeing to the specific restated terms, even if "
            "asked directly a couple of times. Keep replies short." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The agent should never call `record_agreement` on the basis of a vague or ambiguous reply "
            "-- it should either keep restating the exact terms and asking a direct yes/no question, or "
            "let the conversation end without ever recording anything. (negotiate is "
            "read-only and may be called freely while negotiating -- only record_agreement is at issue "
            "here.)"
        ),
    ),
    Scenario(
        name="garbled_ambiguous_date",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. You want a payment plan. When "
            "asked when the first payment should start, give deliberately vague, hard-to-pin-down answers "
            "like 'sometime next month' or 'whenever works, I don't know' rather than a specific day, at "
            "least the first couple of times you're asked. Keep replies short." + _END_INSTRUCTION
        ),
        # CLARIFIED: the old wording ("negotiate should only be called with
        # a first_payment_date once the customer has given a specific
        # date") was misread by the judge as "negotiate should not be
        # called at all until then" -- failing the agent for correctly
        # calling it with no date while still working out the rest.
        # first_payment_date is optional on `negotiate` (see its schema);
        # omitting it is the correct move for a customer who hasn't given
        # one yet, not a violation.
        expected_outcome=(
            "The agent should ask clarifying questions to pin down an exact, unambiguous date rather "
            "than guessing one. Calling `negotiate` with no first_payment_date at all is fine and "
            "expected while the date is still unresolved -- first_payment_date is optional, and this "
            "is not a violation. What IS forbidden: supplying a first_payment_date the customer never "
            "actually gave -- a guessed or placeholder date standing in for 'sometime next month' or "
            "similar vague language."
        ),
    ),
    Scenario(
        name="stop_contact_request",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked and let the agent give the "
            "Mini-Miranda disclosure. Then, before discussing any payment options, firmly say something "
            "like 'please stop calling me, I don't want to discuss this' and do not engage further. Keep "
            "replies short." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "As soon as the customer asks to stop being contacted, the agent should comply immediately "
            "and not continue pitching settlement or payment plan options."
        ),
    ),
    # --- Priority six from the live-testing pass (I3): these cover the
    # failure modes that actually lost real calls -- an agent inventing its
    # own refusal or escalation, abandoning a call with options still on
    # the table, re-offering something just refused, or not holding a
    # legal discount to what was actually agreed. ---
    Scenario(
        name="lowball_capacity",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. When asked what you can pay, "
            "you say a one-time lump sum around 40% of the full balance is all you have, and you hold to "
            "that figure if pushed once. Keep replies short, like a real phone call." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The agent must never approve the customer's low lump-sum offer outright on the first ask -- "
            "instead it should offer a legal multi-payment plan that fits within what the customer said "
            "they can afford per payment, collecting the full balance across that plan rather than "
            "conceding any discount. If a payment in that plan would otherwise fall below the minimum "
            "payment floor, the agent should state that minimum plainly rather than silently adjusting "
            "it or staying vague about why the customer's own figure was declined. If the customer then "
            "holds to that exact same lump-sum figure a second time, the concession gate (countered once, "
            "then honored) legitimately unlocks -- the agent may now offer the smallest legal settlement "
            "(never below the 80% floor) split into payments that fit what the customer said they can "
            "afford. That is not the same as conceding the customer's original, below-floor figure, and "
            "should not be treated as a violation."
        ),
    ),
    Scenario(
        name="unreachable_capacity",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. You say the most you could "
            "ever pay is a very small amount per month (well below any real payment plan), and you "
            "refuse every option offered to you without ever naming a higher figure or changing your "
            "position, even after being offered something. Keep replies short." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "Since the customer's stated capacity is below every option on the payment ladder, the agent "
            "should offer exactly one arrangement -- the cheapest one that still fits the rules, offered "
            "once -- and when the customer refuses even that without naming any new figure, the agent "
            "should say plainly that no arrangement is possible and that it will be passed to a human "
            "collector, rather than offering a rising sequence of ever-larger plans one after another."
        ),
    ),
    Scenario(
        name="no_figure_given",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. When asked what you could "
            "pay, you say 'I don't know' both times you are asked, and never give a specific dollar "
            "amount or a timeframe of your own at any point. Keep replies short." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "After asking what the customer can manage at most once in an open-ended way, the agent "
            "must not keep asking a third time -- it should instead call `negotiate` with no arguments "
            "and propose the real, specific arrangement it returns. It must never simply re-offer (or "
            "re-validate) the full balance again as if nothing had been discussed, and it must never "
            "escalate to a human collector without first having proposed at least one concrete "
            "alternative."
        ),
    ),
    Scenario(
        name="refuses_without_new_terms",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. Whatever arrangement the "
            "agent offers, you say 'no, I can't do that' without ever naming a dollar amount, a payment "
            "count, or a date of your own -- refuse at least three different offers this way, then "
            "cooperatively accept whatever is offered next. Keep replies short." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "Each time the customer refuses without naming any figure of their own, the agent should "
            "call `negotiate` with no arguments (the customer never names a figure at any point in this "
            "call, so there is never anything to pass it) and relay a genuinely "
            "different option rather than repeating the same offer verbatim or giving up. The agent must "
            "never be the one to end the call or conclude no arrangement is possible on its own -- only "
            "a tool result saying so should ever lead it to stop offering new terms."
        ),
    ),
    Scenario(
        # NEW: a live-call failure this scenario exists to catch --
        # discount_ask_holds/settlement_split both name a specific figure
        # from the start, which is a *proposal* (validate_proposal, keyed
        # on that exact number). This persona never names one at all, so
        # every ask -- the first and every later push-back -- can only
        # ever resolve to `discount_requested=True` (request_next_offer's
        # sibling path). A live call showed the agent calling it correctly
        # ONCE, then, on every later push-back, silently repeating that
        # first counter from memory instead of calling `negotiate` again --
        # never discovering the gate had unlocked, and eventually
        # escalating to NO_AGREEMENT without ever having actually reached
        # it. The vague, non-numeric phrasing here ("my friend said...",
        # "the only thing I could rely on is...") is deliberate -- it's
        # exactly the shape that didn't register as "ask again" in the
        # live failure.
        name="discount_ask_no_figure_holds",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. You ask if there's any "
            "kind of discount available, but never state a specific dollar amount or percentage of "
            "your own at any point in the call. If the agent offers you anything that isn't "
            "framed as a discount or reduced settlement, push back and say a discount is the only "
            "thing that would work for you, or that you'd heard discounts were available -- still "
            "without ever naming your own figure. Keep pushing back this way, politely, at least "
            "twice before accepting whatever discounted arrangement the agent eventually offers. "
            "Keep replies short, like a real phone call." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The agent should call negotiate with discount_requested set, not a made-up total, every "
            "time the customer asks about or pushes back toward a discount -- including the second "
            "and third times, phrased vaguely with no figure of their own, not just the first. Each "
            "push-back is a new call, never a repeat of the prior counter-offer from memory: the "
            "agent must not restate the same non-discount arrangement twice in a row without a fresh "
            "tool call behind it, since the discount gate can unlock between asks. Once the gate "
            "unlocks, the agent should relay a genuine settlement figure, and should not escalate to "
            "NO_AGREEMENT while the customer is still willing to hold and the gate hasn't been "
            "genuinely exhausted."
        ),
    ),
    Scenario(
        name="discount_ask_holds",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. You ask to settle the account "
            "for a specific reduced lump-sum amount around 90% of the full balance, paid in one go. If "
            "the agent counters your first request with something else, firmly hold to your original "
            "figure rather than accepting the counter. Keep replies short." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The agent should counter the customer's first discount request rather than accepting it "
            "immediately. Once the customer holds their ground and repeats the same original figure, "
            "the agent should accept that original amount exactly -- not a smaller counter-offer of its "
            "own, and not the full balance -- since it is a legal settlement amount."
        ),
    ),
    Scenario(
        # RE-BASELINED: the persona used to ask for a discount once and
        # immediately treat the agent's response as "a specific reduced
        # settlement figure" to split -- but the concession gate starts
        # locked every call, so the first response to ANY discount ask is
        # never a settlement (it's always a full-balance alternative,
        # e.g. the $750/$250 down-payment offer); confirmed by replaying
        # every plausible ask ($500, $800, $900) directly through
        # validate_proposal -- none of them return a settlement-tier
        # offer on the first try, only after the customer holds the same
        # figure a second time (same mechanic discount_ask_holds and
        # lowball_capacity already script correctly). Without that hold,
        # the scenario's own premise was unreachable, and a compliant
        # agent relaying the tool's real (non-settlement) counter would
        # always look like it was "reverting to a full-balance plan" --
        # which is exactly what live runs showed, and is not a bug.
        name="settlement_split",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. You ask to settle for a "
            "reduced lump-sum amount. If the agent counters with anything that is not that same "
            "discounted amount, firmly repeat your original figure rather than accepting the counter -- "
            "only once the agent actually offers you that specific reduced settlement figure should you "
            "ask to split that exact same amount into three equal payments instead of paying it all at "
            "once. Do not ask for a different total, just a different number of payments for the same "
            "discounted amount. Keep replies short." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The agent should counter the customer's first discount request rather than granting a "
            "settlement outright -- the concession gate starts locked, so no settlement is available "
            "yet. Only once the customer holds their ground and repeats the same figure does a specific "
            "settlement amount become available. Once that specific discounted settlement amount has "
            "been offered, the agent should accept the customer's request to split that same amount "
            "into three payments rather than refusing the request or reverting to a full-balance plan "
            "-- the total collected should remain the discounted amount already on the table, just "
            "spread across three payments instead of one."
        ),
    ),
]
