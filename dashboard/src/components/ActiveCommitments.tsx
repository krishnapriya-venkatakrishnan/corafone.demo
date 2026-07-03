import type { Commitments } from "../types";

export default function ActiveCommitments({ commitments }: { commitments: Commitments | null }) {
  const plans = commitments?.payment_plans ?? [];
  const callbacks = commitments?.scheduled_callbacks ?? [];

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      <div className="rounded-xl bg-neutral-900 border border-neutral-800 p-5">
        <h2 className="text-sm font-medium text-neutral-400 mb-4">Active payment plans</h2>
        {plans.length === 0 ? (
          <p className="text-sm text-neutral-600">None active.</p>
        ) : (
          <ul className="space-y-3">
            {plans.map((plan) => (
              <li
                key={plan.plan_id}
                className="flex items-center justify-between text-sm border-b border-neutral-800/60 pb-3 last:border-0 last:pb-0"
              >
                <div>
                  <p className="text-neutral-200">
                    {plan.num_installments} × ${plan.amount_per_installment.toFixed(2)}
                  </p>
                  <p className="text-xs text-neutral-500">
                    First payment {new Date(plan.start_date).toLocaleDateString()}
                  </p>
                </div>
                <p className="text-neutral-400 tabular-nums">${plan.total_amount.toFixed(2)}</p>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="rounded-xl bg-neutral-900 border border-neutral-800 p-5">
        <h2 className="text-sm font-medium text-neutral-400 mb-4">Upcoming callbacks</h2>
        {callbacks.length === 0 ? (
          <p className="text-sm text-neutral-600">None scheduled.</p>
        ) : (
          <ul className="space-y-3">
            {callbacks.map((cb) => (
              <li
                key={cb.callback_id}
                className="flex items-center justify-between text-sm border-b border-neutral-800/60 pb-3 last:border-0 last:pb-0"
              >
                <p className="text-neutral-200">{new Date(cb.callback_time).toLocaleString()}</p>
                <span className="text-xs text-amber-300 bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 rounded-full">
                  {cb.status}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
