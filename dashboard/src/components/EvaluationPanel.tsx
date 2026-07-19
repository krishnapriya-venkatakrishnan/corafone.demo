import { useEffect, useState } from "react";
import { fetchCalls } from "../api";
import type { CallRecord } from "../types";

const POLL_MS = 3000;

function Badge({ ok, label }: { ok: boolean | null; label: string }) {
  const style =
    ok === null
      ? "bg-idle-bg text-idle-fg border-neutral-200"
      : ok
      ? "bg-pass-bg text-pass-fg border-emerald-200"
      : "bg-fail-bg text-fail-fg border-red-200";
  return (
    <span className={`inline-flex items-center text-xs font-medium px-2.5 py-1 rounded-full border ${style}`}>
      {label}: {ok === null ? "-" : ok ? "Pass" : "Fail"}
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

const DISPOSITION_LABELS: Record<string, string> = {
  SETTLED: "Settled",
  PAYMENT_PLAN_ACTIVE: "Payment plan",
  CALLBACK_SCHEDULED: "Callback scheduled",
  NO_ACTION: "No action",
  ESCALATED_NO_AGREEMENT: "Escalated (no agreement)",
};

export default function EvaluationPanel() {
  const [call, setCall] = useState<CallRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const calls = await fetchCalls();
        if (!cancelled) {
          setCall(calls[0] ?? null);
          setError(null);
        }
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
    return <p className="text-sm text-neutral-500">Loading…</p>;
  }

  if (error) {
    return <div className="text-sm text-fail-fg bg-fail-bg border border-red-200 rounded-lg px-4 py-3">{error}</div>;
  }

  if (!call) {
    return (
      <p className="text-sm text-neutral-500">
        No calls yet - make a call in Voice Agent, then check back here.
      </p>
    );
  }

  // The judge grades in the background after teardown; all its fields land
  // together, so any one of them being null means the rest aren't in yet.
  const judged = call.mini_miranda_passed !== null;

  return (
    <div className="max-w-2xl space-y-6">
      <div className="flex flex-wrap items-center gap-3 text-sm text-neutral-600">
        <span>{call.created_at ? new Date(call.created_at).toLocaleString() : "-"}</span>
        <span className="text-neutral-300">•</span>
        <span>{DISPOSITION_LABELS[call.disposition_code] ?? call.disposition_code}</span>
        <span className="text-neutral-300">•</span>
        <span className="tabular-nums">{call.total_duration_seconds}s</span>
        <span className="text-neutral-300">•</span>
        <span className="tabular-nums">{call.avg_latency_ms}ms avg latency</span>
        <span className="text-neutral-300">•</span>
        <span className="tabular-nums">{call.barge_in_count} barge-ins</span>
        {call.error_count > 0 && (
          <span className="text-fail-fg">{call.error_count} errors</span>
        )}
      </div>

      {!judged ? (
        <p className="text-sm text-periwinkle">Evaluating…</p>
      ) : (
        <>
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
        </>
      )}
    </div>
  );
}
