function Code({ children }: { children: string }) {
  return <code className="bg-neutral-100 rounded px-1 py-0.5 text-[0.9em]">{children}</code>;
}

const LADDER = [
  { tier: "1. Full payment", total: "$1,000", max: "1", example: "$1,000 today" },
  { tier: "2. Downpayment + one", total: "$1,000", max: "2", example: "$750 today, $250 in two weeks" },
  { tier: "3. Settlement", total: "≥ $800 (max 20% off)", max: "3", example: "$266.67 / $266.67 / $266.66" },
  { tier: "4. Payment plan", total: "$1,000", max: "4", example: "$250 × 4 weekly or biweekly; $333.34 × 3 monthly" },
];

export default function DecisionsPanel() {
  return (
    <div className="space-y-12">
      <section className="max-w-[85ch] space-y-6">
        <h2 className="text-lg font-semibold text-black">Three decisions that shaped this build</h2>

        <div className="space-y-3">
          <h3 className="text-base font-semibold text-black">1. Negotiation authority lives outside the agent.</h3>
          <p className="text-sm text-neutral-700 leading-relaxed">
            In collections, the parts that must be correct cannot be left to a probabilistic model.{" "}
            <Code>app/negotiation.py</Code> is pure, deterministic Python - no model call, no database, no
            I/O, and no reads of the system clock - and it is the sole authority on which terms are
            acceptable. The agent's role is narrow by construction: elicit the consumer's proposal, call
            the validator, and relay the verdict. It never judges an amount, and it never composes a
            counter-offer; it speaks the counter the validator returned.
          </p>
          <p className="text-sm text-neutral-700 leading-relaxed">
            The write path re-validates. Agreements are recorded through a single <Code>record_agreement</Code>{" "}
            tool, guarded by one session-level lock, which re-runs the validator server-side before writing
            and refuses anything that does not pass. Even if the model hallucinates an approval, nothing
            invalid reaches the database.
          </p>
        </div>

        <div className="space-y-3">
          <h3 className="text-base font-semibold text-black">2. Compliance is enforced structurally, not by prompt.</h3>
          <p className="text-sm text-neutral-700 leading-relaxed">
            The prompt asks the model to behave; the code makes misbehaviour impossible. Identity is
            resolved server-side from the phone number and is never a parameter the model can set. The
            Mini-Miranda disclosure is a fixed verbatim string. The discount ceiling, the payment floor,
            the payment-count cap and the three-month window are constants the validator enforces - so
            the agent cannot offer unauthorised terms even when it tries.
          </p>
          <p className="text-sm text-neutral-700 leading-relaxed">
            The task states that persuasion relying on threats, false urgency, or invented consequences
            fails regardless of how well it converts. That is enforced in the prompt and then checked
            after the fact: every call is graded by a stronger model against the specific failure modes,
            and the result is stored alongside the call.
          </p>
        </div>

        <div className="space-y-3">
          <h3 className="text-base font-semibold text-black">3. The agent is tested adversarially, against the real prompt.</h3>
          <p className="text-sm text-neutral-700 leading-relaxed">
            The system prompt, tool schemas and tool-handling logic are a single shared core, invoked two
            ways: live over Deepgram with audio, and headless as a text conversation with no Deepgram and
            a mocked database. The scenario suite therefore exercises the <em>production</em> prompt and
            tools, not a copy - a customer persona role-plays hostile scenarios against the real agent,
            and each transcript is graded two ways: an LLM compliance judge, plus structural assertions
            on which tools actually fired and how often.
          </p>
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-black">The negotiation ladder</h2>
        <p className="text-sm text-neutral-700 max-w-[85ch]">Balance: $1,000, 180+ days delinquent.</p>

        <div className="w-full overflow-x-auto rounded-xl border border-neutral-200 max-w-4xl">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-neutral-500 bg-neutral-50">
                <th className="py-3 px-6 font-medium">Tier</th>
                <th className="py-3 px-6 font-medium">Total</th>
                <th className="py-3 px-6 font-medium">Max payments</th>
                <th className="py-3 px-6 font-medium">Example</th>
              </tr>
            </thead>
            <tbody>
              {LADDER.map((row, i) => (
                <tr key={row.tier} className={i > 0 ? "border-t border-neutral-200" : ""}>
                  <td className="py-3.5 px-6 text-black">{row.tier}</td>
                  <td className="py-3.5 px-6 text-neutral-700 tabular-nums">{row.total}</td>
                  <td className="py-3.5 px-6 text-neutral-700 tabular-nums">{row.max}</td>
                  <td className="py-3.5 px-6 text-neutral-500">{row.example}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="text-sm text-neutral-700 leading-relaxed max-w-[85ch]">
          The agent anchors at full payment and steps down only as the consumer resists. It never leads
          with a discount.
        </p>
      </section>

      <section className="max-w-[85ch] space-y-6">
        <h2 className="text-lg font-semibold text-black">Assumptions</h2>

        <div className="space-y-3">
          <p className="text-sm text-neutral-700 leading-relaxed">
            <strong className="text-black">The 25% floor is measured against the original balance.</strong>{" "}
            The task states that the smallest payment can never be less than 25%, without naming the
            base. This is ambiguous only on the settlement tier - every other outcome is full-balance, so
            the two readings give the same figure. On $1,000 settled at 20% off to $800 over three
            payments, $300 / $300 / $200 is valid if the floor is 25% of the settled total ($200) and
            invalid if it is 25% of the original balance ($250). The stricter reading was chosen: it
            cannot grant terms beyond what the task clearly authorises.
          </p>
          <p className="text-sm text-neutral-700 leading-relaxed">
            The counter-argument is reasonable and worth recording - once a settlement is agreed, $800{" "}
            <em>is</em> the obligation, and measuring against a balance no longer being collected requires
            a special case for one tier.
          </p>
        </div>

        <p className="text-sm text-neutral-700 leading-relaxed">
          <strong className="text-black">The maximum number of payments is four, and the task never says so.</strong>{" "}
          It falls out of the floor: at a $250 minimum on $1,000, no arrangement can exceed four payments
          regardless of cadence. Cadence controls the spacing of payments, not how many are possible. On
          the settlement tier the two constraints coincide - at $800 with a $250 floor, at most three
          payments fit, which is exactly that tier's stated cap.
        </p>

        <p className="text-sm text-neutral-700 leading-relaxed">
          <strong className="text-black">"Over 3 months" is read as an exclusive boundary, measured from the last payment.</strong>{" "}
          A payment landing exactly on the three-calendar-month anniversary of the call is outside the
          window, not on its edge. Anchoring on the last payment rather than the first closes a loophole:
          measuring from the first would let a consumer defer the start by two months and then take three
          monthly instalments, stretching an already 180-day delinquent account across five.
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          The exclusive reading costs exactly one arrangement: four monthly payments beginning today span
          92 days, landing the last payment precisely on that anniversary - now illegal. The floor still
          caps every arrangement at four payments, so four still fit; just not on a monthly cadence -
          weekly (28 days) and biweekly (42 days) both clear the window comfortably. A consumer paying
          monthly is offered three payments of $333.34 instead of four of $250.
        </p>

        <p className="text-sm text-neutral-700 leading-relaxed">
          <strong className="text-black">The concession gate counters once, then accepts.</strong> A
          consumer opening with $900 over two payments is proposing a legal 10% settlement, but an
          opening offer is rarely a maximum; accepting immediately forfeits the full balance one further
          ask might have secured. The opposing risk is equally real - pushing back on a 180-day
          delinquent consumer can end the call, turning $900 into nothing, and the task asks for the
          highest-value agreement they will <em>actually honour</em>. So the system counters at most once
          per discount request, then accepts. This cap is deliberate: an agent that never concedes fails
          the "actually honour" test more badly than one that concedes slightly early.
        </p>
      </section>

      <section className="max-w-[85ch] space-y-4">
        <h2 className="text-lg font-semibold text-black">What was tested</h2>
        <p className="text-sm text-neutral-700 leading-relaxed">
          The validator has 42 unit tests covering every tier boundary, the floor, the discount ceiling,
          calendar-month edges either side of the limit, rounding, and degenerate input. It was
          additionally fuzzed across roughly 74,000 generated cases - varying balance, payment count,
          cadence, dates and negotiation state - asserting that it never raises, never returns a
          counter-offer that fails its own validation, and that payments always sum exactly to the total.
          That fuzzing found three real defects, including one where a rounding remainder concentrated on
          the final payment could push it a cent below the floor with no repair able to reach it.
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          The scenario suite runs fourteen adversarial conversations against the live prompt and tools,
          including a consumer who hedges without committing, someone who is not the account holder, a
          request to stop contact, a deflection to "call me back later," and ladder-specific cases like
          holding out on a discount and refusing every offer without ever naming a figure. Each is graded
          by an LLM judge and by structural assertions on tool calls.
        </p>
      </section>

      <section className="max-w-[85ch] space-y-4">
        <h2 className="text-lg font-semibold text-black">Scope and known limitations</h2>
        <p className="text-sm text-neutral-700 leading-relaxed">
          The agent has no callback-scheduling capability. A callback is not one of the acceptable
          outcomes, and removing the tool denies the agent an escape hatch from the negotiation; the
          model is told explicitly that it cannot book one, so it cannot promise a call it is unable to
          make.
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          FDCPA call-frequency limits (no more than seven contacts in seven days) are not enforced. That
          rule is inherently cross-call and belongs in whatever schedules outbound calls, not in a
          per-call judge.
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          A stop-contact request sets a manual-review flag deterministically the moment it is detected.
          With no auto-dialer in this project, there is nothing left to gate on that flag - enforcement
          belongs to the dialer, which is out of scope for a single-call task.
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          The demo account resets to $1,000 and ACTIVE before every call, so evaluators can call
          repeatedly without a prior settlement carrying over. This is a demo affordance, and it also
          clears the manual-review flag.
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          The compliance judge sees only the transcript, not which tools actually fired - tool-related
          guarantees are covered by the structural checks in the scenario suite instead.
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          Deepgram's Voice Agent API owns speech-to-text, turn detection, barge-in and text-to-speech.
          This project owns the collections logic, the negotiation validator, the compliance layer and
          the evaluation harness.
        </p>
      </section>
    </div>
  );
}
