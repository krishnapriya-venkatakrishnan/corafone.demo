import type { AccountSummary } from "../types";

const STATUS_STYLES: Record<string, string> = {
  ACTIVE: "bg-amber-500/10 text-amber-300 border-amber-500/20",
  SETTLED: "bg-emerald-500/10 text-emerald-300 border-emerald-500/20",
  PAYMENT_PLAN_ACTIVE: "bg-sky-500/10 text-sky-300 border-sky-500/20",
  DO_NOT_CALL: "bg-red-500/10 text-red-300 border-red-500/20",
  DISPUTE: "bg-red-500/10 text-red-300 border-red-500/20",
};

export default function AccountOverview({ account }: { account: AccountSummary | null }) {
  return (
    <div className="rounded-xl bg-neutral-900 border border-neutral-800 p-5 flex flex-col gap-4">
      <h2 className="text-sm font-medium text-neutral-400">Account</h2>

      {!account ? (
        <p className="text-sm text-neutral-600">No account found.</p>
      ) : (
        <>
          <div>
            <p className="text-lg font-semibold text-neutral-50">{account.customer_name}</p>
            <p className="text-sm text-neutral-500">{account.phone_number}</p>
          </div>

          <div className="flex items-end justify-between">
            <div>
              <p className="text-xs text-neutral-500 mb-1">Balance</p>
              <p className="text-2xl font-semibold text-neutral-50 tabular-nums">
                ${account.current_balance.toFixed(2)}
              </p>
            </div>
            <span
              className={`text-xs font-medium px-2.5 py-1 rounded-full border ${
                STATUS_STYLES[account.status] ?? "bg-neutral-800 text-neutral-400 border-neutral-700"
              }`}
            >
              {account.status.replaceAll("_", " ")}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
