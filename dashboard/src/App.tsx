import { useEffect, useState } from "react";
import { fetchCalls, fetchCommitments, fetchSummary } from "./api";
import type { CallRecord, Commitments, DashboardSummary } from "./types";
import AccountOverview from "./components/AccountOverview";
import ComplianceRollup from "./components/ComplianceRollup";
import CallHistoryTable from "./components/CallHistoryTable";
import ActiveCommitments from "./components/ActiveCommitments";
import ScenarioRunner from "./components/ScenarioRunner";

export default function App() {
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [calls, setCalls] = useState<CallRecord[]>([]);
  const [commitments, setCommitments] = useState<Commitments | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  async function loadAll() {
    try {
      const [summaryData, callsData, commitmentsData] = await Promise.all([
        fetchSummary(),
        fetchCalls(),
        fetchCommitments(),
      ]);
      setSummary(summaryData);
      setCalls(callsData);
      setCommitments(commitmentsData);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard data.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadAll();
  }, []);

  return (
    <div className="min-h-screen bg-neutral-950 text-neutral-100 font-sans antialiased">
      <div className="pointer-events-none fixed inset-0 flex items-start justify-center overflow-hidden">
        <div className="mt-[-12rem] w-[48rem] h-[48rem] bg-emerald-600/10 rounded-full blur-3xl" />
      </div>

      <div className="relative max-w-6xl mx-auto px-6 py-10 space-y-8">
        <header className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-neutral-50">Corafone</h1>
            <p className="text-sm text-neutral-500">Collections dashboard</p>
          </div>
          <button
            onClick={loadAll}
            className="px-3 py-1.5 rounded-lg text-xs font-medium text-neutral-400 bg-neutral-900 border border-neutral-800 hover:bg-neutral-800 hover:text-neutral-200 transition-colors"
          >
            Refresh
          </button>
        </header>

        {error && (
          <div className="text-sm text-red-400 bg-red-950/40 border border-red-900/60 rounded-lg px-4 py-3">
            {error}
          </div>
        )}

        {loading ? (
          <p className="text-neutral-500 text-sm">Loading…</p>
        ) : (
          <>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <AccountOverview account={summary?.account ?? null} />
              <div className="lg:col-span-2">
                <ComplianceRollup compliance={summary?.compliance ?? null} />
              </div>
            </div>

            <CallHistoryTable calls={calls} />

            <ActiveCommitments commitments={commitments} />

            <ScenarioRunner />
          </>
        )}
      </div>
    </div>
  );
}
