import { useEffect, useState } from "react";
import { fetchScenarios, runScenario, runScenarios } from "../api";
import type { ScenarioInfo, ScenarioResult } from "../types";

function formatScenarioTitle(name: string): string {
  const words = name.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1));
  return `Scenario: ${words.join(" ")}`;
}

export default function ScenarioRunner() {
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>([]);
  const [results, setResults] = useState<Record<string, ScenarioResult>>({});
  const [runningAll, setRunningAll] = useState(false);
  const [runningOne, setRunningOne] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchScenarios()
      .then(setScenarios)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load scenarios."));
  }, []);

  async function handleRunAll() {
    setRunningAll(true);
    setError(null);
    try {
      await runScenarios((event) => {
        if (event.type === "scenario_result") {
          setResults((prev) => ({ ...prev, [event.scenario]: event }));
        }
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scenario run failed.");
    } finally {
      setRunningAll(false);
    }
  }

  async function handleRunOne(name: string) {
    setRunningOne(name);
    setError(null);
    try {
      const result = await runScenario(name);
      setResults((prev) => ({ ...prev, [name]: result }));
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to run ${name}.`);
    } finally {
      setRunningOne(null);
    }
  }

  const anyRunning = runningAll || runningOne !== null;

  return (
    <div className="rounded-xl bg-neutral-900 border border-neutral-800 p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-medium text-neutral-400">Conversation scenario tests</h2>
          <p className="text-xs text-neutral-600 mt-0.5">
            Runs the real prompt and tools against scripted personas -- costs real OpenAI tokens.
          </p>
        </div>
        <button
          onClick={handleRunAll}
          disabled={anyRunning}
          className="px-4 py-2 rounded-lg text-sm font-medium bg-emerald-600 hover:bg-emerald-500 disabled:bg-neutral-800 disabled:text-neutral-600 text-neutral-950 disabled:cursor-not-allowed transition-colors"
        >
          {runningAll ? "Running…" : "Run All Scenarios"}
        </button>
      </div>

      {error && (
        <div className="text-sm text-red-400 bg-red-950/40 border border-red-900/60 rounded-lg px-4 py-3 mb-4">
          {error}
        </div>
      )}

      {scenarios.length === 0 && !error && (
        <p className="text-xs text-neutral-600 text-center">Loading scenarios…</p>
      )}

      <ul className="space-y-2">
        {scenarios.map((scenario) => {
          const result = results[scenario.name];
          const isExpanded = expanded === scenario.name;
          const isRunningThis = runningOne === scenario.name;

          return (
            <li key={scenario.name} className="border border-neutral-800 rounded-lg overflow-hidden">
              <div className="w-full flex items-center justify-between px-4 py-3 gap-3">
                <button
                  onClick={() => setExpanded(isExpanded ? null : scenario.name)}
                  className="flex-1 flex items-center justify-between text-left hover:text-neutral-200 transition-colors"
                >
                  <span className="text-sm text-neutral-200">{formatScenarioTitle(scenario.name)}</span>
                  {result && (
                    <span
                      className={`text-xs font-medium px-2.5 py-1 rounded-full border ${
                        result.passed
                          ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/20"
                          : "bg-red-500/10 text-red-300 border-red-500/20"
                      }`}
                    >
                      {result.passed ? "passed" : "failed"}
                    </span>
                  )}
                </button>
                <button
                  onClick={() => handleRunOne(scenario.name)}
                  disabled={anyRunning}
                  className="shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium text-neutral-400 bg-neutral-800 border border-neutral-700 hover:bg-neutral-700 hover:text-neutral-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {isRunningThis ? "Running…" : "Run"}
                </button>
              </div>
              {isExpanded && (
                <div className="px-4 pb-4 space-y-3 text-xs">
                  <div>
                    <p className="text-neutral-600 uppercase tracking-wide text-[10px] mb-1">Expected outcome</p>
                    <p className="text-neutral-500">{scenario.expected_outcome}</p>
                  </div>
                  {!result ? (
                    <p className="text-neutral-600">Not run yet.</p>
                  ) : (
                    <>
                      {result.hard_failures.length > 0 && (
                        <ul className="text-red-400 list-disc list-inside">
                          {result.hard_failures.map((f, i) => (
                            <li key={i}>{f}</li>
                          ))}
                        </ul>
                      )}
                      <div className="bg-neutral-950/60 border border-neutral-800 rounded-lg p-3">
                        <p className="text-neutral-600 uppercase tracking-wide text-[10px] mb-1">Result</p>
                        <p className={result.passed ? "text-emerald-400" : "text-red-400"}>
                          {result.passed ? "Met expected outcome" : "Did not meet expected outcome"}
                        </p>
                        <p className="text-neutral-500 mt-1">{result.reasoning}</p>
                      </div>
                    </>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
