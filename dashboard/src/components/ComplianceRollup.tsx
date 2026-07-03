import type { ComplianceSummary } from "../types";

function Stat({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <p className="text-xs text-neutral-500">{label}</p>
      <p className={`text-2xl font-semibold tabular-nums ${accent ? "text-emerald-400" : "text-neutral-50"}`}>
        {value}
      </p>
    </div>
  );
}

export default function ComplianceRollup({ compliance }: { compliance: ComplianceSummary | null }) {
  const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);
  const score = (v: number | null) => (v === null ? "—" : v.toFixed(1));
  const cost = (v: number | null) => (v === null ? "—" : `$${v.toFixed(4)}`);

  return (
    <div className="rounded-xl bg-neutral-900 border border-neutral-800 p-5 h-full">
      <h2 className="text-sm font-medium text-neutral-400 mb-4">Compliance rollup</h2>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-6">
        <Stat label="Calls audited" value={String(compliance?.total_calls ?? 0)} />
        <Stat
          label="Mini-Miranda pass rate"
          value={pct(compliance?.mini_miranda_pass_rate ?? null)}
          accent
        />
        <Stat label="Avg tone score" value={score(compliance?.avg_tone_score ?? null)} />
        <Stat
          label="Hallucinations"
          value={String(compliance?.hallucination_count ?? 0)}
        />
        <Stat
          label="Prohibited conduct"
          value={String(compliance?.prohibited_conduct_count ?? 0)}
        />
        <Stat label="Judge spend" value={cost(compliance?.total_judge_cost_usd ?? null)} />
      </div>
    </div>
  );
}
