import { useEffect, useState } from "react";
import { fetchCalls, fetchTranscript } from "../api";
import type { CallRecord } from "../types";

const POLL_MS = 3000;

const DISPOSITION_LABELS: Record<string, string> = {
  SETTLED: "Settled",
  PAYMENT_PLAN_ACTIVE: "Payment plan",
  NO_ACTION: "No action",
  ESCALATED_NO_AGREEMENT: "Escalated (no agreement)",
};

const TIER_LABELS: Record<string, string> = {
  full_payment: "Full payment",
  downpayment_plus_one: "Down payment + one",
  settlement: "Settlement",
  payment_plan: "Payment plan",
};

function formatIsoDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function formatDollars(amount: number): string {
  return amount % 1 === 0 ? `$${amount}` : `$${amount.toFixed(2)}`;
}

function concessionWords(discountCounters: number | null, dateCounters: number | null): string {
  const d = discountCounters ?? 0;
  const t = dateCounters ?? 0;
  if (d === 0 && t === 0) return "accepted the opening offer";
  const parts: string[] = [];
  if (d > 0) parts.push(`held out ${d === 1 ? "once" : `${d} times`} on a discount`);
  if (t > 0) parts.push(`held out ${t === 1 ? "once" : `${t} times`} on a date`);
  return parts.join(" and ");
}

/** True/false/neutral (null) judgment on a compliance call's overall
 * outcome -- used for the call list's pass/fail dot. Mirrors Badge's own
 * null-is-neutral treatment below, just collapsed to one signal. */
function callPassed(call: CallRecord): boolean | null {
  if (call.judge_reasoning === null) return null;
  const failures = [
    call.mini_miranda_passed === false,
    call.hallucination_detected === true,
    call.identity_verified_before_disclosure === false,
    call.prohibited_conduct_detected === true,
    call.right_to_cease_honored === false,
  ];
  return !failures.some(Boolean);
}

function StatusDot({ ok }: { ok: boolean | null }) {
  const color = ok === null ? "bg-idle-fg" : ok ? "bg-pass-fg" : "bg-fail-fg";
  return <span className={`inline-block w-2 h-2 rounded-full shrink-0 ${color}`} />;
}

function Badge({ ok, label }: { ok: boolean | null; label: string }) {
  const style =
    ok === null
      ? "bg-idle-bg text-idle-fg border-neutral-200"
      : ok
      ? "bg-pass-bg text-pass-fg border-emerald-200"
      : "bg-fail-bg text-fail-fg border-red-200";
  return (
    <span className={`inline-flex items-center text-xs font-medium px-2.5 py-1 rounded-full border ${style}`}>
      {label}: {ok === null ? "N/A" : ok ? "Pass" : "Fail"}
    </span>
  );
}

function ToneScore({ score }: { score: number | null }) {
  if (score === null) {
    return <span className="text-xs text-neutral-500">-</span>;
  }
  return (
    <div className="flex items-center gap-1" aria-label={`Tone score ${score} of 5`}>
      {[1, 2, 3, 4, 5].map((n) => (
        <span
          key={n}
          className={`w-2.5 h-2.5 rounded-full ${n <= score ? "bg-periwinkle" : "bg-neutral-200"}`}
        />
      ))}
      <span className="text-xs text-neutral-500 ml-1 tabular-nums">{score}/5</span>
    </div>
  );
}

function CallListRow({
  call,
  active,
  onSelect,
}: {
  call: CallRecord;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left px-4 py-3 border-b border-neutral-100 transition-colors ${
        active ? "bg-periwinkle-tint" : "hover:bg-neutral-50"
      }`}
    >
      <div className="flex items-center gap-2">
        <StatusDot ok={callPassed(call)} />
        <span className="text-sm text-black truncate">
          {call.created_at ? new Date(call.created_at).toLocaleString() : "-"}
        </span>
      </div>
      <p className="text-xs text-neutral-500 mt-0.5 pl-4">
        {DISPOSITION_LABELS[call.disposition_code] ?? call.disposition_code}
      </p>
    </button>
  );
}

function Outcome({ call }: { call: CallRecord }) {
  const payments = call.plan_payments_breakdown?.split(",").filter(Boolean) ?? [];
  const dates = call.plan_payment_dates?.split(",").filter(Boolean) ?? [];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-lg font-semibold text-black">
          {DISPOSITION_LABELS[call.disposition_code] ?? call.disposition_code}
        </span>
        {call.plan_tier && (
          <span className="text-xs font-medium px-2.5 py-1 rounded-full border bg-periwinkle-tint text-periwinkle border-transparent">
            {TIER_LABELS[call.plan_tier] ?? call.plan_tier}
          </span>
        )}
      </div>

      {call.plan_total_amount !== null && (
        <>
          <p className="text-sm text-neutral-700">
            <span className="tabular-nums text-black font-medium">
              {formatDollars(call.plan_total_amount)}
            </span>{" "}
            total across {call.plan_num_installments}{" "}
            {call.plan_num_installments === 1 ? "payment" : "payments"} --{" "}
            {concessionWords(call.plan_discount_counters_issued, call.plan_date_counters_issued)}.
          </p>
          {payments.length > 0 && (
            <ul className="text-sm text-neutral-700 space-y-1 pl-1">
              {payments.map((amount, i) => (
                <li key={i} className="flex gap-2 tabular-nums">
                  <span className="text-black">${amount}</span>
                  {dates[i] && <span className="text-neutral-500">on {formatIsoDate(dates[i])}</span>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {call.disposition_code === "ESCALATED_NO_AGREEMENT" && (
        <p className="text-sm text-neutral-500">
          No arrangement was reached within what the agent could approve -- flagged for manual review.
        </p>
      )}
    </div>
  );
}

function Compliance({ call }: { call: CallRecord }) {
  const judged = call.judge_reasoning !== null;

  if (!judged) {
    return <p className="text-sm text-periwinkle">Evaluating…</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Badge ok={call.mini_miranda_passed} label="Mini-Miranda" />
        <Badge ok={call.identity_verified_before_disclosure} label="Identity verified" />
        <Badge ok={call.pii_redacted_correctly} label="PII handled" />
        <Badge
          ok={call.hallucination_detected === null ? null : !call.hallucination_detected}
          label="No hallucination"
        />
        <Badge
          ok={call.prohibited_conduct_detected === null ? null : !call.prohibited_conduct_detected}
          label="No prohibited conduct"
        />
        <Badge
          ok={call.right_to_cease_honored}
          label={call.right_to_cease_honored === null ? "Stop-contact" : "Stop-contact honored"}
        />
      </div>

      <div className="flex items-center gap-6">
        <div>
          <p className="text-xs text-neutral-500 mb-1.5">Tone</p>
          <ToneScore score={call.tone_score} />
        </div>
        <div>
          <p className="text-xs text-neutral-500 mb-1.5">Judge cost</p>
          <p className="text-sm tabular-nums text-black">
            {call.judge_cost_usd !== null ? `$${call.judge_cost_usd.toFixed(4)}` : "-"}
          </p>
        </div>
      </div>

      {call.judge_reasoning && (
        <div>
          <p className="text-xs text-neutral-500 mb-2">Judge reasoning</p>
          <p className="text-sm text-black leading-relaxed bg-neutral-50 border border-neutral-200 rounded-xl p-4">
            {call.judge_reasoning}
          </p>
        </div>
      )}
    </div>
  );
}

function Transcript({ sessionId }: { sessionId: string }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    if (!open && text === null) {
      setLoading(true);
      setError(null);
      try {
        const response = await fetchTranscript(sessionId);
        setText(response.transcript);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load transcript.");
      } finally {
        setLoading(false);
      }
    }
    setOpen((prev) => !prev);
  }

  return (
    <div>
      <button
        onClick={toggle}
        className="text-sm font-medium text-periwinkle hover:underline"
      >
        {open ? "Hide transcript" : "Show transcript"}
      </button>
      {open && (
        <div className="mt-3 bg-neutral-50 border border-neutral-200 rounded-xl p-4 max-h-96 overflow-y-auto">
          {loading && <p className="text-sm text-neutral-500">Loading…</p>}
          {error && <p className="text-sm text-fail-fg">{error}</p>}
          {text !== null && (
            <pre className="text-xs text-neutral-700 whitespace-pre-wrap font-mono leading-relaxed">
              {text}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

function Metrics({ call }: { call: CallRecord }) {
  return (
    <div className="flex flex-wrap gap-x-8 gap-y-2 text-sm text-neutral-600">
      <span>
        <span className="text-neutral-400">Duration</span>{" "}
        <span className="tabular-nums text-black">{call.total_duration_seconds}s</span>
      </span>
      <span>
        <span className="text-neutral-400">Avg latency</span>{" "}
        <span className="tabular-nums text-black">{call.avg_latency_ms}ms</span>
      </span>
      <span>
        <span className="text-neutral-400">Barge-ins</span>{" "}
        <span className="tabular-nums text-black">{call.barge_in_count}</span>
      </span>
      <span>
        <span className="text-neutral-400">Errors</span>{" "}
        <span className={`tabular-nums ${call.error_count > 0 ? "text-fail-fg" : "text-black"}`}>
          {call.error_count}
        </span>
      </span>
      <span>
        <span className="text-neutral-400">Judge cost</span>{" "}
        <span className="tabular-nums text-black">
          {call.judge_cost_usd !== null ? `$${call.judge_cost_usd.toFixed(4)}` : "-"}
        </span>
      </span>
    </div>
  );
}

export default function CallReportPanel() {
  const [calls, setCalls] = useState<CallRecord[] | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const fetched = await fetchCalls();
        if (cancelled) return;
        setCalls(fetched);
        setError(null);
        // Newest selected by default, on first load only -- a later poll
        // picking up a new call must never swap the view out from under
        // someone reading an earlier one. The polling itself still keeps
        // the SELECTED call's own row (judge fields included) fresh, since
        // `calls` is re-fetched in full every tick and the report below
        // always re-derives from the latest fetch.
        setSelectedSessionId((current) => current ?? fetched[0]?.session_id ?? null);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load calls.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  if (loading) {
    return <p className="text-sm text-neutral-500 px-4 md:px-10">Loading…</p>;
  }

  if (error) {
    return (
      <div className="mx-4 md:mx-10 text-sm text-fail-fg bg-fail-bg border border-red-200 rounded-lg px-4 py-3">
        {error}
      </div>
    );
  }

  if (!calls || calls.length === 0) {
    return (
      <p className="text-sm text-neutral-500 px-4 md:px-10">
        No calls yet - make a call in Voice Agent, then check back here.
      </p>
    );
  }

  const selected = calls.find((c) => c.session_id === selectedSessionId) ?? calls[0];

  return (
    // Below md: the call list collapses from a left column into a compact
    // <select> stacked above the report. At md and up, unchanged -- a
    // left column next to the report.
    <div className="flex flex-col md:flex-row border-t border-neutral-200" style={{ minHeight: "60vh" }}>
      <div className="md:hidden px-4 py-3 border-b border-neutral-200">
        <label htmlFor="call-report-select" className="block text-xs text-neutral-500 mb-1.5">
          Call
        </label>
        <select
          id="call-report-select"
          value={selected.session_id}
          onChange={(e) => setSelectedSessionId(e.target.value)}
          className="w-full px-3 py-2 rounded-lg border border-neutral-200 text-sm text-black bg-white"
        >
          {calls.map((call) => (
            <option key={call.session_id} value={call.session_id}>
              {call.created_at ? new Date(call.created_at).toLocaleString() : "-"} ·{" "}
              {DISPOSITION_LABELS[call.disposition_code] ?? call.disposition_code}
            </option>
          ))}
        </select>
      </div>

      <nav className="hidden md:block w-72 shrink-0 border-r border-neutral-200 overflow-y-auto max-h-[80vh]">
        {calls.map((call) => (
          <CallListRow
            key={call.session_id}
            call={call}
            active={call.session_id === selected.session_id}
            onSelect={() => setSelectedSessionId(call.session_id)}
          />
        ))}
      </nav>

      <div className="flex-1 min-w-0 px-4 md:px-8 py-6 space-y-8 max-w-3xl">
        <Outcome call={selected} />
        <div>
          <p className="text-xs text-neutral-500 mb-2 uppercase tracking-wide">Compliance</p>
          <Compliance call={selected} />
        </div>
        <div>
          <p className="text-xs text-neutral-500 mb-2 uppercase tracking-wide">Transcript</p>
          <Transcript sessionId={selected.session_id} />
        </div>
        <div>
          <p className="text-xs text-neutral-500 mb-2 uppercase tracking-wide">Metrics</p>
          <Metrics call={selected} />
        </div>
      </div>
    </div>
  );
}
