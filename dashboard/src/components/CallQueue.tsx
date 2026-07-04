import type { AccountSummary, QueueRecommendation } from "../types";

interface CallQueueProps {
  active: boolean;
  loading: boolean;
  recommendation: QueueRecommendation | null;
  onStart: () => void;
  onCall: (account: AccountSummary) => void;
  onSkip: () => void;
  onStop: () => void;
}

export default function CallQueue({
  active,
  loading,
  recommendation,
  onStart,
  onCall,
  onSkip,
  onStop,
}: CallQueueProps) {
  return (
    <div className="rounded-xl bg-neutral-900 border border-neutral-800 p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-medium text-neutral-400">Agentic call queue</h2>
          <p className="text-xs text-neutral-600 mt-0.5">
            An agent picks which account to call next from recent call history -- you still attend and end
            every call.
          </p>
        </div>
        {!active ? (
          <button
            onClick={onStart}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-emerald-600 hover:bg-emerald-500 text-neutral-950 transition-colors"
          >
            Start queue
          </button>
        ) : (
          <button
            onClick={onStop}
            className="px-3 py-1.5 rounded-lg text-xs font-medium text-neutral-400 bg-neutral-800 border border-neutral-700 hover:bg-neutral-700 hover:text-neutral-200 transition-colors"
          >
            Stop queue
          </button>
        )}
      </div>

      {active && (
        <div className="border border-neutral-800 rounded-lg p-4">
          {loading ? (
            <p className="text-sm text-neutral-600">Finding the next account to call…</p>
          ) : !recommendation?.account ? (
            <p className="text-sm text-neutral-500">{recommendation?.reasoning ?? "No eligible accounts."}</p>
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-neutral-300">
                Agent recommends calling{" "}
                <span className="text-neutral-50 font-medium">{recommendation.account.customer_name}</span>{" "}
                next
              </p>
              <p className="text-xs text-neutral-500 leading-relaxed">{recommendation.reasoning}</p>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => onCall(recommendation.account!)}
                  className="px-4 py-1.5 rounded-lg text-xs font-medium bg-emerald-600 hover:bg-emerald-500 text-neutral-950 transition-colors"
                >
                  Call
                </button>
                <button
                  onClick={onSkip}
                  className="px-4 py-1.5 rounded-lg text-xs font-medium text-neutral-400 bg-neutral-800 border border-neutral-700 hover:bg-neutral-700 hover:text-neutral-200 transition-colors"
                >
                  Skip
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
