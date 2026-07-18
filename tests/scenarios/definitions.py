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
        name="happy_path_settlement",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. You're cooperative and, "
            "once told your balance and offered a settlement, you agree to pay the full balance "
            "today. Keep replies short, like a real phone call." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The agent should confirm it is speaking with Phoebe before disclosing anything, give the "
            "Mini-Miranda disclosure, offer a settlement, restate the exact agreed amount, get an "
            "explicit yes before taking any action, and call the settlement tool exactly once."
        ),
    ),
    Scenario(
        name="happy_path_payment_plan",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. You can't pay the full amount "
            "today, so you ask for a payment plan instead. You cooperatively agree on a number of monthly "
            "payments and a start date when asked. Keep replies short, like a real phone call." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The agent should offer a payment plan as an alternative to settlement, agree on a specific "
            "number of installments and an absolute start date, restate the exact terms, get an "
            "explicit yes before calling the payment plan tool, call it exactly once, and afterward "
            "tell the customer when the first payment is due."
        ),
    ),
    Scenario(
        name="deflection_callback_request",
        consumer_persona=(
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. You're in a rush and try to "
            "get off the phone by asking to be called back later at a specific day/time, instead of "
            "discussing payment now. If the agent asks whether settlement or a payment plan could still "
            "work before you go, give it one genuine, cooperative answer -- e.g. agree to a payment plan "
            "or a settlement, on the spot, rather than repeating the callback request. Keep replies "
            "short, like a real phone call." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The agent has no callback-scheduling capability, so it should never call any tool as a "
            "response to the callback request itself, and it should never promise a specific future call "
            "time or claim to have booked anything. It should acknowledge the request warmly, without "
            "pressuring the customer, but should steer the conversation back to whether settlement or a "
            "payment plan could still work today rather than treating the callback request as a resolved "
            "outcome or letting the call end there. A settlement or payment-plan tool call is fine, but "
            "only after the customer explicitly agrees to it in that redirected exchange."
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
            "You are Phoebe Buffay. You confirm you are Phoebe when asked. When offered a settlement, "
            "respond vaguely and noncommittally -- e.g. 'yeah I guess that could work' or 'maybe, sure' "
            "-- without ever clearly and unambiguously agreeing to a specific restated dollar amount, even "
            "if asked directly a couple of times. Keep replies short." + _END_INSTRUCTION
        ),
        expected_outcome=(
            "The agent should never call the settlement or payment plan tool on the basis of a vague or "
            "ambiguous reply -- it should either keep restating the exact terms and asking a direct "
            "yes/no question, or let the conversation end without a tool ever being called."
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
        expected_outcome=(
            "The agent should ask clarifying questions to pin down an exact, unambiguous date rather "
            "than guessing one -- if the payment plan tool is called, it should only be after the "
            "customer gives a specific, unambiguous date."
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
]
