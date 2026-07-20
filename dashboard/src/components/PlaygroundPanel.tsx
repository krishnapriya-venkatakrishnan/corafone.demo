import { useState } from "react";
import { validateProposal } from "../api";
import type { Cadence, ValidateResponse } from "../types";

const today = () => new Date().toISOString().slice(0, 10);

interface FormState {
  totalAmount: string;
  numberOfPayments: string;
  cadence: Cadence;
  firstPaymentDate: string;
  discountAlreadyCountered: boolean;
  // Comma-separated per-payment amounts, e.g. "600, 400". Empty means "no
  // explicit split" -- numberOfPayments drives an even split as before.
  // Non-empty is authoritative: it drives the payment count instead.
  paymentsText: string;
}

const DEFAULT_FORM: FormState = {
  totalAmount: "1000",
  numberOfPayments: "1",
  cadence: "once",
  firstPaymentDate: today(),
  discountAlreadyCountered: false,
  paymentsText: "",
};

function parsePaymentsEntries(text: string): string[] {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
}

function formatMoney(n: number): string {
  return `$${n.toLocaleString("en-US", { maximumFractionDigits: 2 })}`;
}

// Null means "nothing to validate" (empty) or "valid". Checked at submit
// time so a mismatched split never reaches the network -- the backend
// would just reject it with the same information, later and slower.
function paymentsValidationError(paymentsText: string, totalAmount: string): string | null {
  const entries = parsePaymentsEntries(paymentsText);
  if (entries.length === 0) return null;

  const values = entries.map(Number);
  if (values.some((v) => !Number.isFinite(v) || v <= 0)) {
    return "Every payment must be a positive number.";
  }

  const total = Number(totalAmount);
  const sum = values.reduce((a, b) => a + b, 0);
  if (Number.isFinite(total) && Math.round(sum * 100) !== Math.round(total * 100)) {
    return `These add up to ${formatMoney(sum)}, but the total is ${formatMoney(total)}`;
  }
  return null;
}

// Labeled by what the consumer said, not which rule ends up firing -- a
// label tied to a violation code can be contradicted by whichever codes
// actually come back (see "Instalments too small", which also trips a
// payment-count violation). What the consumer proposed never changes.
const PRESETS: { label: string; form: FormState }[] = [
  {
    label: "Pay in full",
    form: { totalAmount: "1000", numberOfPayments: "1", cadence: "once", firstPaymentDate: today(), discountAlreadyCountered: false, paymentsText: "" },
  },
  {
    label: "Lowball - $600 today",
    form: { totalAmount: "600", numberOfPayments: "1", cadence: "once", firstPaymentDate: today(), discountAlreadyCountered: false, paymentsText: "" },
  },
  {
    label: "Instalments too small",
    form: { totalAmount: "800", numberOfPayments: "4", cadence: "monthly", firstPaymentDate: today(), discountAlreadyCountered: true, paymentsText: "" },
  },
  {
    label: "Too many instalments",
    form: { totalAmount: "1000", numberOfPayments: "6", cadence: "biweekly", firstPaymentDate: today(), discountAlreadyCountered: false, paymentsText: "" },
  },
  // Same total, same count, two different splits -- an even $500/$500 is
  // legal, but $900/$100 trips payment_below_floor even though the total
  // never changed. That distinction is invisible unless per-payment
  // amounts are shown, not just the total and count (see the result panel).
  {
    label: "Uneven split",
    form: { totalAmount: "1000", numberOfPayments: "2", cadence: "biweekly", firstPaymentDate: today(), discountAlreadyCountered: false, paymentsText: "600, 400" },
  },
  {
    label: "Below floor",
    form: { totalAmount: "1000", numberOfPayments: "2", cadence: "biweekly", firstPaymentDate: today(), discountAlreadyCountered: false, paymentsText: "900, 100" },
  },
];

// Plain-language gloss for each machine-readable code, shown alongside the
// raw code rather than instead of it -- the whole point of surfacing
// violations here is operator insight, so a code like `degenerate_input`
// (correct but opaque for a six-payment request -- the sanity cap fires
// before tier logic ever runs) needs a reading a non-author can use.
const VIOLATION_DESCRIPTIONS: Record<string, string> = {
  degenerate_input: "Outside acceptable range -- rejected before tier-specific rules even run",
  discount_too_deep: "Discount exceeds the 20% settlement ceiling",
  overpayment: "Total exceeds the account balance",
  payment_below_floor: "A payment is below the 25% minimum",
  too_many_payments: "More instalments than this tier allows",
  duration_exceeds_window: "Last payment falls outside the 3-month window",
  first_payment_too_late: "First payment is more than 14 days out",
  first_payment_in_past: "First payment date is in the past",
  discount_gate_locked: "First discount request -- countered once before any discount is accepted",
  no_agreement_possible: "Every reachable arrangement has already been offered and refused",
};

function DecisionBadge({ decision }: { decision: "ACCEPT" | "COUNTER" | "NO_AGREEMENT" }) {
  const style =
    decision === "ACCEPT"
      ? "bg-pass-bg text-pass-fg border-emerald-200"
      : decision === "NO_AGREEMENT"
        ? "bg-fail-bg text-fail-fg border-red-200"
        : "bg-info-bg text-info-fg border-sky-200";
  return (
    <span className={`inline-flex items-center text-sm font-semibold px-3 py-1 rounded-full border ${style}`}>
      {decision}
    </span>
  );
}

export default function PlaygroundPanel() {
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [result, setResult] = useState<ValidateResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const paymentsEntries = parsePaymentsEntries(form.paymentsText);
  const paymentsFilled = paymentsEntries.length > 0;
  const paymentsError = paymentsValidationError(form.paymentsText, form.totalAmount);

  async function submit(f: FormState) {
    const entries = parsePaymentsEntries(f.paymentsText);
    if (paymentsValidationError(f.paymentsText, f.totalAmount)) {
      // Inline error is already rendered from `form` below -- nothing to
      // send, the backend would just reject this the same way, later.
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const payments = entries.length > 0 ? entries.map(Number) : undefined;
      const response = await validateProposal({
        total_amount: Number(f.totalAmount),
        number_of_payments: payments ? payments.length : Number(f.numberOfPayments),
        cadence: f.cadence,
        first_payment_date: f.firstPaymentDate,
        discount_already_countered: f.discountAlreadyCountered,
        ...(payments ? { payments } : {}),
      });
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Validation request failed.");
    } finally {
      setLoading(false);
    }
  }

  function applyPreset(preset: FormState) {
    setForm(preset);
    submit(preset);
  }

  return (
    <div className="space-y-6">
      <p className="text-sm text-neutral-600 max-w-[70ch] leading-relaxed">
        This calls the same deterministic validator the live agent calls - the agent has no authority to
        accept or counter anything on its own.
      </p>

      <div className="flex flex-wrap gap-2">
        {PRESETS.map((preset) => (
          <button
            key={preset.label}
            onClick={() => applyPreset(preset.form)}
            className="px-3 py-1.5 rounded-lg text-xs font-medium text-neutral-600 bg-neutral-100 border border-neutral-200 hover:bg-neutral-200 hover:text-black transition-colors"
          >
            {preset.label}
          </button>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Inputs */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            submit(form);
          }}
          className="space-y-4 max-w-sm"
        >
          <div>
            <label className="block text-xs font-medium text-neutral-600 mb-1">Total amount ($)</label>
            <input
              type="number"
              step="0.01"
              min="0"
              value={form.totalAmount}
              onChange={(e) => setForm({ ...form, totalAmount: e.target.value })}
              className="w-full px-3 py-2 rounded-lg border border-neutral-200 text-sm text-black focus:outline-none focus:ring-2 focus:ring-periwinkle-soft"
            />
          </div>

          <div>
            <label className="block text-xs font-medium text-neutral-600 mb-1">
              Per-payment amounts (optional)
            </label>
            <input
              type="text"
              placeholder="600, 400"
              value={form.paymentsText}
              onChange={(e) => setForm({ ...form, paymentsText: e.target.value })}
              className="w-full px-3 py-2 rounded-lg border border-neutral-200 text-sm text-black focus:outline-none focus:ring-2 focus:ring-periwinkle-soft"
            />
            {paymentsError ? (
              <p className="text-xs text-fail-fg mt-1">{paymentsError}</p>
            ) : paymentsFilled ? (
              <p className="text-xs text-neutral-500 mt-1">
                {paymentsEntries.length} payment{paymentsEntries.length === 1 ? "" : "s"} derived from this list.
              </p>
            ) : null}
          </div>

          <div>
            <label className="block text-xs font-medium text-neutral-600 mb-1">Number of payments</label>
            <input
              type="number"
              step="1"
              min="1"
              value={paymentsFilled ? paymentsEntries.length : form.numberOfPayments}
              disabled={paymentsFilled}
              onChange={(e) => setForm({ ...form, numberOfPayments: e.target.value })}
              className="w-full px-3 py-2 rounded-lg border border-neutral-200 text-sm text-black focus:outline-none focus:ring-2 focus:ring-periwinkle-soft disabled:bg-neutral-100 disabled:text-neutral-400"
            />
            {paymentsFilled && (
              <p className="text-xs text-neutral-500 mt-1">Driven by the per-payment list above.</p>
            )}
          </div>

          <div>
            <label className="block text-xs font-medium text-neutral-600 mb-1">Cadence</label>
            <select
              value={form.cadence}
              onChange={(e) => setForm({ ...form, cadence: e.target.value as Cadence })}
              className="w-full px-3 py-2 rounded-lg border border-neutral-200 text-sm text-black focus:outline-none focus:ring-2 focus:ring-periwinkle-soft"
            >
              <option value="once">Once</option>
              <option value="weekly">Weekly</option>
              <option value="biweekly">Biweekly</option>
              <option value="monthly">Monthly</option>
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-neutral-600 mb-1">First payment date</label>
            <input
              type="date"
              value={form.firstPaymentDate}
              onChange={(e) => setForm({ ...form, firstPaymentDate: e.target.value })}
              className="w-full px-3 py-2 rounded-lg border border-neutral-200 text-sm text-black focus:outline-none focus:ring-2 focus:ring-periwinkle-soft"
            />
          </div>

          <label className="flex items-start gap-2 text-sm text-neutral-700">
            <input
              type="checkbox"
              checked={form.discountAlreadyCountered}
              onChange={(e) => setForm({ ...form, discountAlreadyCountered: e.target.checked })}
              className="mt-0.5"
            />
            Consumer has already been countered once on a discount.
          </label>

          <button
            type="submit"
            disabled={loading || !!paymentsError}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-periwinkle hover:bg-periwinkle-soft disabled:bg-neutral-100 disabled:text-neutral-400 text-white transition-colors"
          >
            {loading ? "Checking…" : "Check"}
          </button>

          {error && (
            <div className="text-sm text-fail-fg bg-fail-bg border border-red-200 rounded-lg px-4 py-3">
              {error}
            </div>
          )}
        </form>

        {/* Verdict */}
        <div className="space-y-5">
          {!result ? (
            <p className="text-sm text-neutral-500">Submit terms to see the verdict.</p>
          ) : (
            <>
              <DecisionBadge decision={result.decision} />

              <div>
                <p className="text-xs text-neutral-500 mb-1.5">What the agent would say</p>
                <p className="text-sm text-black leading-relaxed bg-neutral-50 border border-neutral-200 rounded-xl p-4">
                  "{result.reason}"
                </p>
              </div>

              {result.offer && (
                <div>
                  <p className="text-xs text-neutral-500 mb-2">Resulting schedule ({result.offer.tier.replaceAll("_", " ")})</p>
                  <div className="w-full overflow-x-auto rounded-xl border border-neutral-200">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-left text-xs text-neutral-500 bg-neutral-50">
                          <th className="py-2 px-4 font-medium">#</th>
                          <th className="py-2 px-4 font-medium">Amount</th>
                          <th className="py-2 px-4 font-medium">Date</th>
                        </tr>
                      </thead>
                      <tbody>
                        {result.offer.payments.map((amount, i) => (
                          <tr key={i} className={i > 0 ? "border-t border-neutral-200" : ""}>
                            <td className="py-2 px-4 text-neutral-500">{i + 1}</td>
                            <td className="py-2 px-4 text-black tabular-nums">${amount}</td>
                            <td className="py-2 px-4 text-neutral-700 tabular-nums">{result.offer!.dates[i]}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {result.agent_note && (
                <div>
                  <p className="text-xs text-neutral-500 mb-1.5">
                    Diagnostic note -- the agent reads this, but never speaks it
                  </p>
                  <p className="text-xs text-neutral-500 bg-idle-bg border border-neutral-200 rounded-lg px-3 py-2">
                    {result.agent_note}
                  </p>
                </div>
              )}

              <div>
                <p className="text-xs text-neutral-500 mb-1.5">
                  Internal diagnostic codes -- withheld from the agent, shown here for review
                </p>
                {result.violations.length === 0 ? (
                  <p className="text-xs text-idle-fg bg-idle-bg border border-neutral-200 rounded-lg px-3 py-2 inline-block">
                    none
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    {result.violations.map((v) => (
                      <div
                        key={v}
                        className="flex items-baseline gap-2 text-xs bg-idle-bg border border-neutral-200 rounded-lg px-3 py-1.5"
                      >
                        <span className="text-idle-fg">{VIOLATION_DESCRIPTIONS[v] ?? v}</span>
                        <span className="text-neutral-400">({v})</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
