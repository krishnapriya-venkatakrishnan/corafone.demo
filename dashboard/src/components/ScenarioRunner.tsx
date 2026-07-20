import { useEffect, useState } from "react";
import { fetchScenarios, runScenario, runScenarios } from "../api";
import type { ScenarioInfo, ScenarioResult } from "../types";

function formatScenarioTitle(name: string): string {
  const words = name.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1));
  return `Scenario: ${words.join(" ")}`;
}

// A crashed run (OpenAI rate limit, timeout, network error) is not a
// compliance failure -- it never produced a verdict at all, so it must
// render distinctly (grey, "not run") and never be counted as pass or
// fail, in either the per-row badge or the summary line below.
function summarize(results: Record<string, ScenarioResult>) {
  const values = Object.values(results);
  const crashed = values.filter((r) => r.crashed).length;
  const judged = values.filter((r) => !r.crashed);
  const passed = judged.filter((r) => r.passed).length;
  return { total: values.length, judged: judged.length, passed, crashed };
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
    <div className="max-w-3xl">
      <p className="text-sm text-neutral-600 mb-5 leading-relaxed">
        These scenarios run the real system prompt and tools as a text conversation - no Deepgram,
        and against a mocked database.
      </p>

      <div className="rounded-xl bg-white border border-neutral-200 p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-sm font-medium text-black">Conversation scenario tests</h2>
            <p className="text-xs text-neutral-500 mt-0.5">
              Runs the real prompt and tools against scripted personas -- costs real OpenAI tokens.
            </p>
            {Object.keys(results).length > 0 && (() => {
              const { judged, passed, crashed } = summarize(results);
              return (
                <p className="text-xs text-neutral-500 mt-1.5">
                  <span className="font-medium text-black">
                    {passed}/{judged}
                  </span>{" "}
                  passed
                  {crashed > 0 && (
                    <span className="text-idle-fg"> · {crashed} not run (infrastructure error)</span>
                  )}
                </p>
              );
            })()}
          </div>
          <button
            onClick={handleRunAll}
            disabled={anyRunning}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-periwinkle hover:bg-periwinkle-soft disabled:bg-neutral-100 disabled:text-neutral-400 text-white disabled:cursor-not-allowed transition-colors"
          >
            {runningAll ? "Running…" : "Run All Scenarios"}
          </button>
        </div>

        {error && (
          <div className="text-sm text-fail-fg bg-fail-bg border border-red-200 rounded-lg px-4 py-3 mb-4">
            {error}
          </div>
        )}

        {scenarios.length === 0 && !error && (
          <p className="text-xs text-neutral-500 text-center">Loading scenarios…</p>
        )}

        <ul className="space-y-2">
          {scenarios.map((scenario) => {
            const result = results[scenario.name];
            const isExpanded = expanded === scenario.name;
            const isRunningThis = runningOne === scenario.name;

            return (
              <li key={scenario.name} className="border border-neutral-200 rounded-lg overflow-hidden">
                <div className="w-full flex items-center justify-between px-4 py-3 gap-3">
                  <button
                    onClick={() => setExpanded(isExpanded ? null : scenario.name)}
                    className="flex-1 flex items-center justify-between text-left hover:text-periwinkle transition-colors"
                  >
                    <span className="text-sm text-black">{formatScenarioTitle(scenario.name)}</span>
                    {result && (
                      <span
                        className={`text-xs font-medium px-2.5 py-1 rounded-full border ${
                          result.crashed
                            ? "bg-idle-bg text-idle-fg border-neutral-200"
                            : result.passed
                            ? "bg-pass-bg text-pass-fg border-emerald-200"
                            : "bg-fail-bg text-fail-fg border-red-200"
                        }`}
                      >
                        {result.crashed ? "not run" : result.passed ? "passed" : "failed"}
                      </span>
                    )}
                  </button>
                  <button
                    onClick={() => handleRunOne(scenario.name)}
                    disabled={anyRunning}
                    className="shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium text-neutral-600 bg-neutral-100 border border-neutral-200 hover:bg-neutral-200 hover:text-black disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {isRunningThis ? "Running…" : "Run"}
                  </button>
                </div>
                {isExpanded && (
                  <div className="px-4 pb-4 space-y-3 text-xs">
                    <div>
                      <p className="text-neutral-500 uppercase tracking-wide text-[10px] mb-1">Expected outcome</p>
                      <p className="text-neutral-600">{scenario.expected_outcome}</p>
                    </div>
                    {!result ? (
                      <p className="text-neutral-500">Not run yet.</p>
                    ) : result.crashed ? (
                      <div className="bg-idle-bg border border-neutral-200 rounded-lg p-3">
                        <p className="text-idle-fg uppercase tracking-wide text-[10px] mb-1">
                          Not run -- infrastructure error
                        </p>
                        <p className="text-neutral-600">
                          This scenario never reached a verdict (an OpenAI API failure, not a
                          compliance result) -- it's excluded from the pass count above. Use Run to
                          retry it individually.
                        </p>
                        {result.error && (
                          <p className="text-neutral-500 mt-1 font-mono text-[11px] break-all">
                            {result.error}
                          </p>
                        )}
                      </div>
                    ) : (
                      <>
                        {result.hard_failures.length > 0 && (
                          <ul className="text-fail-fg list-disc list-inside">
                            {result.hard_failures.map((f, i) => (
                              <li key={i}>{f}</li>
                            ))}
                          </ul>
                        )}
                        <div className="bg-neutral-50 border border-neutral-200 rounded-lg p-3">
                          <p className="text-neutral-500 uppercase tracking-wide text-[10px] mb-1">Result</p>
                          <p className={result.passed ? "text-pass-fg" : "text-fail-fg"}>
                            {result.passed ? "Met expected outcome" : "Did not meet expected outcome"}
                          </p>
                          <p className="text-neutral-600 mt-1">{result.reasoning}</p>
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
    </div>
  );
}
