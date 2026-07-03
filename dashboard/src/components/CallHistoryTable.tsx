import { useState } from "react";
import type { CallRecord } from "../types";
import { fetchTranscript } from "../api";

const DISPOSITION_STYLES: Record<string, string> = {
  SETTLED: "bg-emerald-500/10 text-emerald-300 border-emerald-500/20",
  PAYMENT_PLAN_ACTIVE: "bg-sky-500/10 text-sky-300 border-sky-500/20",
  CALLBACK_SCHEDULED: "bg-amber-500/10 text-amber-300 border-amber-500/20",
  NO_ACTION: "bg-neutral-800 text-neutral-400 border-neutral-700",
};

function Badge({ ok, label }: { ok: boolean | null; label: string }) {
  if (ok === null) {
    return <span className="text-neutral-600">{label}: —</span>;
  }
  return (
    <span className={ok ? "text-emerald-400" : "text-red-400"}>
      {label}: {ok ? "yes" : "no"}
    </span>
  );
}

function CallRow({ call }: { call: CallRecord }) {
  const [expanded, setExpanded] = useState(false);
  const [transcript, setTranscript] = useState<string | null>(null);
  const [loadingTranscript, setLoadingTranscript] = useState(false);

  async function toggle() {
    setExpanded((prev) => !prev);
    if (!expanded && transcript === null && call.transcript_path) {
      setLoadingTranscript(true);
      try {
        const res = await fetchTranscript(call.session_id);
        setTranscript(res.transcript);
      } catch {
        setTranscript("Failed to load transcript.");
      } finally {
        setLoadingTranscript(false);
      }
    }
  }

  return (
    <>
      <tr
        onClick={toggle}
        className="border-t border-neutral-800 cursor-pointer hover:bg-neutral-800/40 transition-colors"
      >
        <td className="py-3 px-4 text-sm text-neutral-400 whitespace-nowrap">
          {call.created_at ? new Date(call.created_at).toLocaleString() : "—"}
        </td>
        <td className="py-3 px-4">
          <span
            className={`text-xs font-medium px-2.5 py-1 rounded-full border ${
              DISPOSITION_STYLES[call.disposition_code] ?? DISPOSITION_STYLES.NO_ACTION
            }`}
          >
            {call.disposition_code.replaceAll("_", " ")}
          </span>
        </td>
        <td className="py-3 px-4 text-sm text-neutral-300 tabular-nums">{call.total_duration_seconds}s</td>
        <td className="py-3 px-4 text-sm text-neutral-300 tabular-nums">{call.avg_latency_ms}ms</td>
        <td className="py-3 px-4 text-sm text-neutral-300 tabular-nums">{call.barge_in_count}</td>
        <td className="py-3 px-4 text-sm tabular-nums">
          {call.error_count > 0 ? (
            <span className="text-red-400">{call.error_count}</span>
          ) : (
            <span className="text-neutral-500">0</span>
          )}
        </td>
        <td className="py-3 px-4 text-sm">
          {call.mini_miranda_passed === null ? (
            <span className="text-neutral-600">pending</span>
          ) : call.mini_miranda_passed ? (
            <span className="text-emerald-400">pass</span>
          ) : (
            <span className="text-red-400">fail</span>
          )}
        </td>
        <td className="py-3 px-4 text-sm text-neutral-300 tabular-nums">
          {call.tone_score ?? "—"}
        </td>
        <td className="py-3 px-4 text-sm text-neutral-500 tabular-nums">
          {call.judge_cost_usd !== null ? `$${call.judge_cost_usd.toFixed(4)}` : "—"}
        </td>
      </tr>
      {expanded && (
        <tr className="border-t border-neutral-800/60 bg-neutral-950/40">
          <td colSpan={9} className="px-4 py-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 text-sm">
              <div className="space-y-1.5">
                <p className="text-xs text-neutral-500 mb-2">Judge assessment</p>
                <Badge ok={call.identity_verified_before_disclosure} label="Identity verified" />
                <br />
                <Badge ok={call.pii_redacted_correctly} label="PII handled correctly" />
                <br />
                <Badge ok={call.hallucination_detected === null ? null : !call.hallucination_detected} label="No hallucination" />
                <br />
                <Badge ok={call.prohibited_conduct_detected === null ? null : !call.prohibited_conduct_detected} label="No prohibited conduct" />
                <br />
                <Badge
                  ok={call.right_to_cease_honored}
                  label={call.right_to_cease_honored === null ? "Stop-contact request" : "Stop-contact honored"}
                />
                {call.judge_reasoning && (
                  <p className="text-neutral-400 mt-3 leading-relaxed">{call.judge_reasoning}</p>
                )}
              </div>
              <div>
                <p className="text-xs text-neutral-500 mb-2">Transcript</p>
                {!call.transcript_path ? (
                  <p className="text-neutral-600">No transcript recorded.</p>
                ) : loadingTranscript ? (
                  <p className="text-neutral-600">Loading…</p>
                ) : (
                  <pre className="whitespace-pre-wrap text-neutral-400 text-xs leading-relaxed max-h-64 overflow-y-auto bg-neutral-900 border border-neutral-800 rounded-lg p-3">
                    {transcript}
                  </pre>
                )}
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function CallHistoryTable({ calls }: { calls: CallRecord[] }) {
  return (
    <div className="rounded-xl bg-neutral-900 border border-neutral-800 overflow-hidden">
      <div className="p-5 pb-0">
        <h2 className="text-sm font-medium text-neutral-400">Call history</h2>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full mt-3">
          <thead>
            <tr className="text-left text-xs text-neutral-500">
              <th className="py-2 px-4 font-medium">Time</th>
              <th className="py-2 px-4 font-medium">Disposition</th>
              <th className="py-2 px-4 font-medium">Duration</th>
              <th className="py-2 px-4 font-medium">Avg latency</th>
              <th className="py-2 px-4 font-medium">Barge-ins</th>
              <th className="py-2 px-4 font-medium">Errors</th>
              <th className="py-2 px-4 font-medium">Mini-Miranda</th>
              <th className="py-2 px-4 font-medium">Tone</th>
              <th className="py-2 px-4 font-medium">Judge cost</th>
            </tr>
          </thead>
          <tbody>
            {calls.length === 0 ? (
              <tr>
                <td colSpan={9} className="py-8 text-center text-sm text-neutral-600">
                  No calls yet.
                </td>
              </tr>
            ) : (
              calls.map((call) => <CallRow key={call.session_id} call={call} />)
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
