import { Fragment } from "react";
import type { AccountSummary, CallRecord, Commitments } from "../types";
import CallHistoryTable from "./CallHistoryTable";
import ActiveCommitments from "./ActiveCommitments";

const STATUS_STYLES: Record<string, string> = {
  ACTIVE: "bg-amber-500/10 text-amber-300 border-amber-500/20",
  SETTLED: "bg-emerald-500/10 text-emerald-300 border-emerald-500/20",
  PAYMENT_PLAN_ACTIVE: "bg-sky-500/10 text-sky-300 border-sky-500/20",
  DO_NOT_CALL: "bg-red-500/10 text-red-300 border-red-500/20",
  DISPUTE: "bg-red-500/10 text-red-300 border-red-500/20",
};

interface AccountsTableProps {
  accounts: AccountSummary[];
  expandedAccountId: number | null;
  onToggleAccount: (accountId: number) => void;
  loadingDetail: boolean;
  detailCalls: CallRecord[];
  detailCommitments: Commitments | null;
}

export default function AccountsTable({
  accounts,
  expandedAccountId,
  onToggleAccount,
  loadingDetail,
  detailCalls,
  detailCommitments,
}: AccountsTableProps) {
  return (
    <div className="rounded-xl bg-neutral-900 border border-neutral-800 overflow-hidden">
      <div className="p-5 pb-0">
        <h2 className="text-sm font-medium text-neutral-400">Accounts</h2>
        <p className="text-xs text-neutral-600 mt-0.5">
          Click an account to see its calls, payment plans, and callbacks.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full mt-3">
          <thead>
            <tr className="text-left text-xs text-neutral-500">
              <th className="py-2 px-4 font-medium">Name</th>
              <th className="py-2 px-4 font-medium">Phone</th>
              <th className="py-2 px-4 font-medium">Balance</th>
              <th className="py-2 px-4 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {accounts.length === 0 ? (
              <tr>
                <td colSpan={4} className="py-8 text-center text-sm text-neutral-600">
                  No accounts found.
                </td>
              </tr>
            ) : (
              accounts.map((account) => {
                const isExpanded = account.account_id === expandedAccountId;
                return (
                  <Fragment key={account.account_id}>
                    <tr
                      onClick={() => onToggleAccount(account.account_id)}
                      className={`border-t border-neutral-800 cursor-pointer transition-colors ${
                        isExpanded ? "bg-neutral-800/40" : "hover:bg-neutral-800/40"
                      }`}
                    >
                      <td className="py-3 px-4 text-sm text-neutral-200">{account.customer_name}</td>
                      <td className="py-3 px-4 text-sm text-neutral-400">{account.phone_number}</td>
                      <td className="py-3 px-4 text-sm text-neutral-300 tabular-nums">
                        ${account.current_balance.toFixed(2)}
                      </td>
                      <td className="py-3 px-4">
                        <span
                          className={`text-xs font-medium px-2.5 py-1 rounded-full border ${
                            STATUS_STYLES[account.status] ?? "bg-neutral-800 text-neutral-400 border-neutral-700"
                          }`}
                        >
                          {account.status.replaceAll("_", " ")}
                        </span>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr className="border-t border-neutral-800/60 bg-neutral-950/40">
                        <td colSpan={4} className="px-4 py-5 space-y-5">
                          {loadingDetail ? (
                            <p className="text-sm text-neutral-600">Loading…</p>
                          ) : (
                            <>
                              <CallHistoryTable calls={detailCalls} />
                              <ActiveCommitments commitments={detailCommitments} />
                            </>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
