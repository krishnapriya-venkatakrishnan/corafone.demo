const OFFERS = [
  { tier: "Pay in full", amount: "$1,000 today", note: "Where every call starts." },
  {
    tier: "A down payment plus one",
    amount: "$750 today, $250 in two weeks",
    note: "The split is not a choice: the second payment is pushed to the minimum so the first is as large as possible.",
  },
  {
    tier: "A settlement",
    amount: "$800 to $1,000, up to three payments",
    note: "Only if the consumer asks for a discount, and only after being told no once.",
  },
  {
    tier: "A payment plan",
    amount: "$1,000 across two to four payments",
    note: "Weekly, biweekly or monthly, all inside three months.",
  },
];

const NEVER = [
  "Threaten. Invent a deadline or a consequence.",
  "Promise a callback: there is nothing behind such a promise, so it is never made.",
  "Say a number the validator did not give it.",
  "Discuss the debt with anyone who is not the account holder. Not the balance, not the reason for the call, not even that it is a collections call. If the wrong person answers, the agent asks when the account holder is reachable and ends politely.",
  "Keep going after someone asks it to stop.",
];

export default function DecisionsPanel() {
  return (
    <div className="space-y-12">
      <section className="max-w-[85ch] space-y-4">
        <h2 className="text-lg font-semibold text-black">The core idea</h2>
        <p className="text-base text-black leading-relaxed">
          <strong>The agent talks. The code decides.</strong>
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          The voice you hear runs the conversation: it listens, responds, and keeps things natural. But
          it has no authority over money. Every amount, every date, every yes or no comes from a separate
          piece of code that the model cannot argue with.
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          Ask the agent for a discount and it does not decide. It asks the validator, and repeats the
          answer.
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          That separation is the whole design. It means the rules hold no matter what the model does,
          what the consumer says, or how the conversation goes.
        </p>
      </section>

      <section className="space-y-4">
        <h2 className="text-lg font-semibold text-black">What the agent can offer</h2>
        <p className="text-sm text-neutral-700 max-w-[85ch]">The account is $1,000, 180 days overdue. No single payment can be below $250.</p>

        <div className="w-full overflow-x-auto rounded-xl border border-neutral-200 max-w-4xl">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-neutral-500 bg-neutral-50">
                <th className="py-3 px-6 font-medium">Offer</th>
                <th className="py-3 px-6 font-medium">Terms</th>
                <th className="py-3 px-6 font-medium">Notes</th>
              </tr>
            </thead>
            <tbody>
              {OFFERS.map((row, i) => (
                <tr key={row.tier} className={i > 0 ? "border-t border-neutral-200" : ""}>
                  <td className="py-3.5 px-6 text-black align-top whitespace-nowrap">{row.tier}</td>
                  <td className="py-3.5 px-6 text-neutral-700 align-top whitespace-nowrap">{row.amount}</td>
                  <td className="py-3.5 px-6 text-neutral-500 align-top">{row.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="max-w-[85ch] space-y-4">
        <h2 className="text-lg font-semibold text-black">How it picks</h2>
        <p className="text-sm text-neutral-700 leading-relaxed">
          Not by working down a list. The code builds every legal arrangement, then removes:
        </p>
        <ul className="text-sm text-neutral-700 space-y-1.5 list-disc list-inside leading-relaxed">
          <li>anything already offered and turned down</li>
          <li>anything the consumer cannot afford, based on what they said they can manage</li>
          <li>anything that will not fit inside three months</li>
          <li>settlements, unless a discount was genuinely requested</li>
        </ul>
        <p className="text-sm text-neutral-700 leading-relaxed">
          From what is left, it takes the one that <strong className="text-black">collects the most money the consumer can actually pay.</strong>
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          That last part is deliberate. A settlement collects less ($800) but asks for <em>more</em> per
          payment ($266.67) than the cheapest plan ($250). So for someone short on cash, the plan is both
          better for them and better for us. Ranking strictly by the list would have handed out discounts
          to people who never needed one.
        </p>
      </section>

      <section className="max-w-[85ch] space-y-6">
        <h2 className="text-lg font-semibold text-black">Three things that took the most thought</h2>

        <div className="space-y-2">
          <h3 className="text-base font-semibold text-black">Discounts are paced, not switched on</h3>
          <p className="text-sm text-neutral-700 leading-relaxed">
            The first time someone asks for a reduction, the answer is no, with a real alternative
            attached. Only if they hold does the discount tier open. Then it steps down gradually: 5%,
            then 10%, then 15%, then 20%, rather than jumping to the maximum.
          </p>
          <p className="text-sm text-neutral-700 leading-relaxed">
            "Up to 20% off" means <em>up to</em>. Conceding the full amount on the first ask gives away
            money nobody asked for.
          </p>
          <p className="text-sm text-neutral-700 leading-relaxed">
            The same pacing applies to dates. Ask to start a month out and you are offered something
            sooner, once. Hold, and it is accepted, as long as everything still lands inside three
            months.
          </p>
        </div>

        <div className="space-y-2">
          <h3 className="text-base font-semibold text-black">The consumer's own offer is kept exactly</h3>
          <p className="text-sm text-neutral-700 leading-relaxed">
            Propose $600 today and $400 later, and that is what gets recorded, not $500 and $500. The
            system checks <em>every individual payment</em> against the $250 minimum, not just the total.
          </p>
          <p className="text-sm text-neutral-700 leading-relaxed">
            That distinction matters. $1,000 split evenly across two payments is $500 each and perfectly
            fine. The same $1,000 split $900 and $100 is not, and only a per-payment check catches it.
          </p>
          <p className="text-sm text-neutral-700 leading-relaxed">
            Try both in the <strong className="text-black">Validator</strong> tab.
          </p>
        </div>

        <div className="space-y-2">
          <h3 className="text-base font-semibold text-black">"Three months" is read strictly</h3>
          <p className="text-sm text-neutral-700 leading-relaxed">
            A payment landing exactly on the three-month anniversary counts as outside the window.
          </p>
          <p className="text-sm text-neutral-700 leading-relaxed">
            It changes one thing: four monthly payments of $250 span 92 days, so that arrangement is not
            allowed. The cheapest monthly plan is three payments of $333.34. Four payments of $250 are
            still available weekly or biweekly.
          </p>
          <p className="text-sm text-neutral-700 leading-relaxed">
            The stricter reading was chosen deliberately: "three months max" is a maximum.
          </p>
        </div>
      </section>

      <section className="max-w-[85ch] space-y-3">
        <h2 className="text-lg font-semibold text-black">What the agent will never do</h2>
        <ul className="text-sm text-neutral-700 space-y-1.5 list-disc list-inside leading-relaxed">
          {NEVER.map((item, i) => (
            <li key={i}>{item}</li>
          ))}
        </ul>
      </section>

      <section className="max-w-[85ch] space-y-4">
        <h2 className="text-lg font-semibold text-black">How this was tested</h2>
        <p className="text-sm text-neutral-700 leading-relaxed">
          <strong className="text-black">The rules themselves:</strong> 279 automated tests, plus tens of
          thousands of randomised proposals thrown at the validator every run, checking it never crashes,
          never offers something that breaks its own rules, and never produces payments that do not add
          up.
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          <strong className="text-black">The conversation:</strong> fourteen scripted difficult consumers,
          someone who hedges without committing, the wrong person answering, someone asking not to be
          called again, someone who will only pay a fraction, someone pushing hard for a discount, someone
          giving vague dates. Each is played by a language model against the real agent, and a second
          model reads the transcript and judges whether the agent held the line.
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          Alongside that, automatic checks: did the agent say a number no tool gave it? Did it record an
          agreement nobody agreed to? Did it end the call when the rules said it still had options?
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          <strong className="text-black">Real calls</strong>, from a browser and from a phone.
        </p>
        <p className="text-sm text-neutral-700 leading-relaxed">
          The test suite found genuine bugs, including one in itself. A scenario expected the agent to
          accept a 25% discount when the cap is 20%. The validator refused, the test failed, and the{" "}
          <em>test</em> was wrong.
        </p>
      </section>

      <section className="max-w-[85ch] space-y-3">
        <h2 className="text-lg font-semibold text-black">Try it yourself</h2>
        <ul className="text-sm text-neutral-700 space-y-1.5 leading-relaxed">
          <li>
            <strong className="text-black">Validator:</strong> type in any offer and see the verdict,
            with the reason. No call required.
          </li>
          <li>
            <strong className="text-black">Call Report:</strong> every call, its transcript, what was
            agreed, and the compliance audit.
          </li>
          <li>
            <strong className="text-black">Test Suite:</strong> the difficult-consumer scenarios and how
            the agent handled each.
          </li>
        </ul>
      </section>
    </div>
  );
}
