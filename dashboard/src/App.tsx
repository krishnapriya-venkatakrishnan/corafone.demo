import { useEffect, useState } from "react";
import { fetchAccounts, fetchCalls, fetchCommitments, fetchNextInQueue, fetchSummary } from "./api";
import type { AccountSummary, CallRecord, Commitments, ComplianceSummary, QueueRecommendation } from "./types";
import AccountsTable from "./components/AccountsTable";
import CallModal from "./components/CallModal";
import CallQueue from "./components/CallQueue";
import ScenarioRunner from "./components/ScenarioRunner";

export default function App() {
  const [accounts, setAccounts] = useState<AccountSummary[]>([]);
  const [expandedAccountId, setExpandedAccountId] = useState<number | null>(null);
  const [callAccount, setCallAccount] = useState<AccountSummary | null>(null);
  const [compliance, setCompliance] = useState<ComplianceSummary | null>(null);
  const [detailCalls, setDetailCalls] = useState<CallRecord[]>([]);
  const [detailCommitments, setDetailCommitments] = useState<Commitments | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [queueActive, setQueueActive] = useState(false);
  const [queueLoading, setQueueLoading] = useState(false);
  const [queueExcludeIds, setQueueExcludeIds] = useState<number[]>([]);
  const [queueRecommendation, setQueueRecommendation] = useState<QueueRecommendation | null>(null);

  async function loadComplianceFor(accountId: number | null) {
    const summary = await fetchSummary(accountId);
    setCompliance(summary.compliance);
  }

  async function loadDetailFor(accountId: number) {
    setLoadingDetail(true);
    try {
      const [callsData, commitmentsData] = await Promise.all([
        fetchCalls(accountId),
        fetchCommitments(accountId),
      ]);
      setDetailCalls(callsData);
      setDetailCommitments(commitmentsData);
    } finally {
      setLoadingDetail(false);
    }
  }

  async function loadDashboardData() {
    try {
      const accountsData = await fetchAccounts();
      setAccounts(accountsData);
      if (expandedAccountId !== null) {
        await Promise.all([loadComplianceFor(expandedAccountId), loadDetailFor(expandedAccountId)]);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard data.");
    }
  }

  async function refresh() {
    setLoading(true);
    try {
      await loadDashboardData();
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleToggleAccount(accountId: number) {
    const nextId = expandedAccountId === accountId ? null : accountId;
    setExpandedAccountId(nextId);
    setError(null);
    // Clear immediately, before the new account's data arrives -- otherwise
    // a slow or failed fetch leaves the previously-expanded account's calls/
    // commitments visible under the newly-expanded row.
    setDetailCalls([]);
    setDetailCommitments(null);
    setCompliance(null);

    if (nextId === null) return;

    // Independent requests: one failing (e.g. an account with no calls yet)
    // shouldn't block the other from rendering.
    const results = await Promise.allSettled([loadComplianceFor(nextId), loadDetailFor(nextId)]);
    const failure = results.find((r): r is PromiseRejectedResult => r.status === "rejected");
    if (failure) {
      setError(
        failure.reason instanceof Error ? failure.reason.message : "Failed to load account detail."
      );
    }
  }

  async function loadNextInQueue(excludeIds: number[]) {
    setQueueLoading(true);
    try {
      const recommendation = await fetchNextInQueue(excludeIds);
      setQueueRecommendation(recommendation);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load the next queued account.");
    } finally {
      setQueueLoading(false);
    }
  }

  function handleStartQueue() {
    setQueueActive(true);
    setQueueExcludeIds([]);
    loadNextInQueue([]);
  }

  function handleStopQueue() {
    setQueueActive(false);
    setQueueRecommendation(null);
    setQueueExcludeIds([]);
  }

  function handleSkipQueued() {
    if (!queueRecommendation?.account) return;
    const nextExcludeIds = [...queueExcludeIds, queueRecommendation.account.account_id];
    setQueueExcludeIds(nextExcludeIds);
    loadNextInQueue(nextExcludeIds);
  }

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
            onClick={refresh}
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
            <CallQueue
              active={queueActive}
              loading={queueLoading}
              recommendation={queueRecommendation}
              onStart={handleStartQueue}
              onCall={setCallAccount}
              onSkip={handleSkipQueued}
              onStop={handleStopQueue}
            />

            <AccountsTable
              accounts={accounts}
              expandedAccountId={expandedAccountId}
              onToggleAccount={handleToggleAccount}
              loadingDetail={loadingDetail}
              detailCompliance={compliance}
              detailCalls={detailCalls}
              detailCommitments={detailCommitments}
            />

            <ScenarioRunner />
          </>
        )}
      </div>

      {callAccount && (
        <CallModal
          account={callAccount}
          onClose={() => {
            // Capture before clearing -- needed to tell whether this was the
            // queue's own recommended call (vs. a manual click elsewhere)
            // and, if so, to advance to the next one.
            const wasQueuedCall =
              queueActive && queueRecommendation?.account?.account_id === callAccount.account_id;
            const justCalledId = callAccount.account_id;

            setCallAccount(null);
            // Refresh in place (no full-page "Loading…" flash) so the just-
            // finished call's data (balance/status/history) shows immediately.
            loadDashboardData();

            if (wasQueuedCall) {
              const nextExcludeIds = [...queueExcludeIds, justCalledId];
              setQueueExcludeIds(nextExcludeIds);
              loadNextInQueue(nextExcludeIds);
            }
          }}
        />
      )}
    </div>
  );
}
