# Corafone — voice collections agent

A voice agent that negotiates repayment on a delinquent account, where **every number it says is decided by deterministic code, not by the model.**

**Talk to the agent:** [corafone-demo.vercel.app](https://corafone-demo.vercel.app/) — talk to the agent, read call reports, inspect the validator
**Direct call link:** [corafone-demo.vercel.app/call/](https://corafone-demo.vercel.app/call/)

---

## The one-sentence version

The model runs the conversation. A separate Python module decides what is acceptable. The model can only speak figures a tool gave it — it cannot invent, approve, or refuse an amount on its own.

---

## How it works

```
Consumer speech
      |
Deepgram Voice Agent  (speech-to-text, turn-taking, barge-in, text-to-speech)
      |
   gpt-4o  — runs the conversation, calls tools, never decides amounts
      |
   negotiate()  — deterministic. No LLM, no clock, no database.
      |            Returns ACCEPT / COUNTER / NO_AGREEMENT
      |
record_agreement()  — re-validates, then writes to Postgres
      |
gpt-4o post-call audit  — scores the transcript for FDCPA compliance
```

The negotiation module (`app/negotiation.py`) is a pure function. Same inputs, same outputs, every time. It has no access to the model, the database, or the current time — the call date is passed in. That is what makes it testable, and what lets the same code back the dashboard's validator playground.

---

## The negotiation ladder

Account: **$1,000**, 180+ days delinquent. Smallest payment: **$250** (25%).

| | Outcome | Total | Payments | Notes |
|---|---|---|---|---|
| 1 | Full payment | $1,000 | 1 | Opening position, always |
| 2 | Down payment + one | $1,000 | 2 | $750 today, $250 later — the split is fixed by the floor |
| 3 | Settlement | $800–$1,000 | 1–3 | Gated: only after an explicit discount request |
| 4 | Payment plan | $1,000 | 2–4 | Weekly, biweekly or monthly, inside 3 months |

**Selection is capacity-scored, not scripted.** The module enumerates every legal arrangement, discards what has been refused, what the consumer cannot afford, and what will not fit the calendar — then picks the one collecting the most money that they can actually pay.

That last point matters. The tiers are not monotonic in value: a settlement collects **less** ($800) than a payment plan ($1,000) while demanding **more** per payment ($266.67 vs $250). Following the list literally would concede $200 to consumers who could afford the full balance. So the module orders by value collected, using the task's tier order to break ties between equal totals.

---

## Compliance

Enforced in code and prompt, audited after every call.

**Identity gate.** No balance, no purpose, no disclosure until the account holder is confirmed. Under FDCPA §1692c(b) a collector may not reveal that a debt exists to a third party — so withholding is the *correct* behaviour on a wrong-number call, and the audit scores it as not-applicable rather than failed.

**Mini-Miranda**, verbatim, immediately after identity is confirmed.

**Stop-contact** honoured immediately, from any point in the call, overriding everything.

**No threats, no false urgency, no invented consequences.** The agent never claims a lawsuit, a deadline, or a credit consequence. It never promises a callback — there is no scheduler, so any such claim would be a promise with nothing behind it.

**Disputes** get one neutral confirmation, then the call is logged and ended. A disputed debt is not negotiated.

Every call is scored by a gpt-4o audit against these criteria, stored in `ai_evaluation_logs` and visible in the dashboard's Call Report.

---

## Assumptions

The brief leaves several things open. Each of these was a deliberate choice.

**Smallest payment = 25% of the original balance ($250)**, not 25% of the negotiated total. The stricter reading; it makes the floor a fixed protection rather than one that shrinks with the discount.

**"Over 3 months max" is read exclusively.** A payment landing exactly on the three-month anniversary is *outside* the window. This affects exactly one arrangement — $250 × 4 monthly, which spans 92 days — so the cheapest monthly plan is $333.34 × 3. Four payments of $250 remain available weekly and biweekly.

**The window is measured in calendar months from the call date to the last payment**, not in days. Four monthly payments span 92 days; a flat 90-day rule would reject the standard monthly plan on a quirk of month lengths.

**First payment defaults to today** when no date is given. A date more than 14 days out is countered once with an earlier one, then accepted if the consumer holds — bounded always by the three-month window. The 14 days is ours, not the task's: on a 180-day delinquent account a distant promise carries real default risk, but refusing a payday-aligned date would produce plans that break on the first instalment.

**A settlement is never offered unprompted.** The discount tier stays locked until the consumer explicitly asks for a reduction and holds after one counter. Once open, the module walks intermediate steps — 5%, 10%, 15%, then 20% — rather than conceding the maximum immediately. "Up to 20% off" means up to, not always.

**A consumer's own legal proposal is accepted exactly as stated.** $600 today and $400 later is accepted as $600/$400, not normalised to the canonical $750/$250. If they propose $900 and it is legal, they get $900, not the $800 ceiling.

---

## Testing

**279 unit tests** covering the module, the tool handlers, persistence, the audit, and the voice-agent event loop.

**Property-based fuzzing** — tens of thousands of randomised proposals per run, asserting four invariants: the module never raises, never returns a counter-offer that fails its own validation, payments always sum exactly to the total, and `NO_AGREEMENT` is the only decision that returns no offer.

**An adversarial scenario harness.** Fifteen personas — hedging, wrong number, stop-contact, lowball, unreachable capacity, discount pressure, garbled dates — driven by an LLM playing an uncooperative consumer against the real prompt and real tools, with an LLM judge scoring each transcript against a written expectation. Structural checks run alongside: every figure the agent speaks must appear in a tool result or the consumer's own words; `record_agreement` may never fire without a matching prior acceptance; escalation phrasing may never appear without a `NO_AGREEMENT` verdict. It runs from pytest (`pytest -m scenario`). It isn't exposed in the dashboard because a full run saturates the API rate limit on this account tier.

The harness found real bugs — including one in its own expectations. One scenario demanded a 25% discount when the cap is 20%; the module refused, the test failed, and the *test* was wrong.

**Live calls**, from browser and phone, against the deployed instance.

---

## Known limitations

**Model adherence.** The agent can occasionally speak an offer without calling the validator first. Nothing invalid can be *recorded* — `record_agreement` re-validates server-side and rejects — but an unauthorised figure can be spoken. This was materially worse on gpt-4o-mini; moving to gpt-4o appears to reduce it — a live harness pass found zero ungrounded figures in the scenarios that completed — but gpt-4o's own rate limit (see Testing) capped that pass at 2 of 15 scenarios before crashing, so this is an observation, not a measured rate. The cost of the switch either way is higher latency and a much tighter rate limit.

**Interruption detection under-reports.** Deepgram signals end-of-transmission, not end-of-playback, so a consumer interrupting during buffered audio may not be counted as a barge-in.

**Splits on intermediate settlements are not proposed.** If the consumer asks for $950 across three payments it is validated and accepted; the module simply does not offer that shape unprompted.

**Monitoring and follow-up are out of scope.** Agreements are recorded with their full schedules and flagged where the first payment is deferred, but missed-payment detection and follow-up contact belong to dialler and payment systems this project does not include.

**Demo affordance:** each call resets the account so it can be tested repeatedly. Prior agreements are marked superseded rather than deleted, so history accumulates.

---

## Repo

```
app/negotiation.py   the deterministic module — start here
app/tools.py         tool handlers, turn memoisation, gate-shopping guard
app/config.py        system prompt and tool schemas
app/voice_agent.py   Deepgram event loop, barge-in, transcript, teardown
app/audit.py         post-call FDCPA audit
app/database/        schema
dashboard/           React dashboard — call UI, reports, validator
tests/               unit, fuzz, and scenario suites (harness runs via `pytest -m scenario`, not the dashboard)
```

## Running locally

```bash
pip install -r requirements.txt
# set DEEPGRAM_API_KEY, OPENAI_API_KEY, DATABASE_URL,
#     SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, TZ=America/New_York
uvicorn app.main:app --reload
python -m pytest tests/ -q       # Layer 1: unit + fuzz, free and instant
pytest -m scenario               # Layer 3: adversarial harness, costs real OpenAI tokens (see Testing)
```

The dashboard's **Playground** tab exercises `negotiation.py` directly — no model, no database, no call required. It is the fastest way to see the rules for yourself.
