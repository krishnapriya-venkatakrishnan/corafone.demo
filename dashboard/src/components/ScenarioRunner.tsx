import { useState } from "react";
import { runScenarios } from "../api";
import type { ScenarioResultEvent } from "../types";

export default function ScenarioRunner() {
  const [trials, setTrials] = useState(1);
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<ScenarioResultEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  async function handleRun() {
    setRunning(true);
    setResults([]);
    setError(null);
    try {
      await runScenarios(trials, (event) => {
        if (event.type === "scenario_result") {
          setResults((prev) => [...prev, event]);
        }
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scenario run failed.");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="rounded-xl bg-neutral-900 border border-neutral-800 p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-medium text-neutral-400">Conversation scenario tests</h2>
          <p className="text-xs text-neutral-600 mt-0.5">
            Runs the real prompt and tools against 8 scripted personas -- costs real OpenAI tokens.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-neutral-500">
            Trials
            <select
              value={trials}
              onChange={(e) => setTrials(Number(e.target.value))}
              disabled={running}
              className="bg-neutral-800 border border-neutral-700 rounded-md px-2 py-1 text-neutral-200"
            >
              <option value={1}>1</option>
              <option value={3}>3</option>
              <option value={5}>5</option>
            </select>
          </label>
          <button
            onClick={handleRun}
            disabled={running}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-emerald-600 hover:bg-emerald-500 disabled:bg-neutral-800 disabled:text-neutral-600 text-neutral-950 disabled:cursor-not-allowed transition-colors"
          >
            {running ? "Running…" : "Run Scenarios"}
          </button>
        </div>
      </div>

      {error && (
        <div className="text-sm text-red-400 bg-red-950/40 border border-red-900/60 rounded-lg px-4 py-3 mb-4">
          {error}
        </div>
      )}

      {results.length === 0 && !running && (
        <p className="text-xs text-neutral-600 text-center">No results yet -- click Run Scenarios.</p>
      )}

      <ul className="space-y-2">
        {results.map((result) => {
          const passed = result.judge_passes >= Math.ceil((result.trials * 2) / 3) && result.hard_failures.length === 0;
          const isExpanded = expanded === result.scenario;
          return (
            <li key={result.scenario} className="border border-neutral-800 rounded-lg overflow-hidden">
              <button
                onClick={() => setExpanded(isExpanded ? null : result.scenario)}
                className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-neutral-800/40 transition-colors"
              >
                <span className="text-sm text-neutral-200">{result.scenario.replaceAll("_", " ")}</span>
                <span className="flex items-center gap-3">
                  {result.hard_failures.length > 0 && (
                    <span className="text-xs text-red-400">structural issue</span>
                  )}
                  <span
                    className={`text-xs font-medium px-2.5 py-1 rounded-full border ${
                      passed
                        ? "bg-emerald-500/10 text-emerald-300 border-emerald-500/20"
                        : "bg-red-500/10 text-red-300 border-red-500/20"
                    }`}
                  >
                    {result.judge_passes}/{result.trials} passed
                  </span>
                </span>
              </button>
              {isExpanded && (
                <div className="px-4 pb-4 space-y-3 text-xs">
                  <p className="text-neutral-500">{result.expected_outcome}</p>
                  {result.hard_failures.length > 0 && (
                    <ul className="text-red-400 list-disc list-inside">
                      {result.hard_failures.map((f, i) => (
                        <li key={i}>{f}</li>
                      ))}
                    </ul>
                  )}
                  {result.trial_details.map((trial) => (
                    <div key={trial.trial} className="bg-neutral-950/60 border border-neutral-800 rounded-lg p-3">
                      <p className={trial.outcome_met ? "text-emerald-400" : "text-red-400"}>
                        Trial {trial.trial + 1}: {trial.outcome_met ? "met expected outcome" : "did not meet expected outcome"}
                      </p>
                      <p className="text-neutral-500 mt-1">{trial.reasoning}</p>
                    </div>
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
