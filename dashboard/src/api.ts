import type {
  AccountSummary,
  CallRecord,
  Commitments,
  DashboardSummary,
  ScenarioEvent,
  ScenarioInfo,
  ScenarioResult,
  TranscriptResponse,
} from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

// The voice demo (frontend/), a separate app -- this dashboard is read-only
// and never talks to Deepgram/the mic directly; the call modal embeds it
// via an iframe instead (see components/CallModal.tsx).
export const FRONTEND_BASE = import.meta.env.VITE_FRONTEND_BASE ?? "http://localhost:8080";

async function getJSON<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    throw new Error(`${path} failed: ${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

const accountQuery = (accountId: number | null) =>
  accountId === null ? "" : `?account_id=${accountId}`;

export const fetchAccounts = () => getJSON<AccountSummary[]>("/api/dashboard/accounts");
export const fetchSummary = (accountId: number | null = null) =>
  getJSON<DashboardSummary>(`/api/dashboard/summary${accountQuery(accountId)}`);
export const fetchCalls = (accountId: number | null = null) =>
  getJSON<CallRecord[]>(`/api/dashboard/calls${accountQuery(accountId)}`);
export const fetchCommitments = (accountId: number | null = null) =>
  getJSON<Commitments>(`/api/dashboard/commitments${accountQuery(accountId)}`);
export const fetchTranscript = (sessionId: string) =>
  getJSON<TranscriptResponse>(`/api/dashboard/calls/${sessionId}/transcript`);

export const fetchScenarios = () => getJSON<ScenarioInfo[]>("/api/dashboard/scenarios");
export const runScenario = (name: string) =>
  getJSON<ScenarioResult>(`/api/dashboard/scenarios/run/${encodeURIComponent(name)}`);

/** Consumes the run-all SSE stream, calling `onEvent` for each scenario as
 * it completes and resolving once the server sends "done". */
export async function runScenarios(onEvent: (event: ScenarioEvent) => void): Promise<void> {
  const response = await fetch(`${API_BASE}/api/dashboard/scenarios/run`);
  if (!response.ok || !response.body) {
    throw new Error(`scenarios/run failed: ${response.status} ${response.statusText}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line; each starts with "data: ".
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith("data:")) continue;
      const event = JSON.parse(line.slice("data:".length).trim()) as ScenarioEvent;
      onEvent(event);
      if (event.type === "done") return;
    }
  }
}
