import { Fragment } from "react";
import type { AccountSummary, CallRecord, Commitments, ComplianceSummary } from "../types";
import CallHistoryTable from "./CallHistoryTable";
import ActiveCommitments from "./ActiveCommitments";
import ComplianceRollup from "./ComplianceRollup";

const STATUS_STYLES: Record<string, string> = {
  ACTIVE: "bg-amber-500/10 text-amber-300 border-amber-500/20",
  SETTLED: "bg-emerald-500/10 text-emerald-300 border-emerald-500/20",
  PAYMENT_PLAN_ACTIVE: "bg-sky-500/10 text-sky-300 border-sky-500/20",
  DO_NOT_CALL: "bg-red-500/10 text-red-300 border-red-500/20",
  DISPUTE: "bg-red-500/10 text-red-300 border-red-500/20",
};

function CallIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"></path>
    </svg>
  );
}

interface AccountsTableProps {
  accounts: AccountSummary[];
  expandedAccountId: number | null;
  onToggleAccount: (accountId: number) => void;
  onCall: (account: AccountSummary) => void;
  loadingDetail: boolean;
  detailCompliance: ComplianceSummary | null;
  detailCalls: CallRecord[];
  detailCommitments: Commitments | null;
}

export default function AccountsTable({
  accounts,
  expandedAccountId,
  onToggleAccount,
  onCall,
  loadingDetail,
  detailCompliance,
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
              <th className="py-2 px-4 font-medium text-right">Call</th>
            </tr>
          </thead>
          <tbody>
            {accounts.length === 0 ? (
              <tr>
                <td colSpan={5} className="py-8 text-center text-sm text-neutral-600">
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
                      <td className="py-3 px-4 text-right">
                        {account.status !== "SETTLED" && (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onCall(account);
                            }}
                            aria-label={`Call ${account.customer_name}`}
                            className="inline-flex w-8 h-8 rounded-full bg-emerald-600 hover:bg-emerald-500 text-neutral-950 items-center justify-center transition-colors"
                          >
                            <CallIcon />
                          </button>
                        )}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr className="border-t border-neutral-800/60 bg-neutral-950/40">
                        <td colSpan={5} className="px-4 py-5 space-y-5">
                          {loadingDetail ? (
                            <p className="text-sm text-neutral-600">Loading…</p>
                          ) : (
                            <>
                              <ComplianceRollup compliance={detailCompliance} scopeLabel={account.customer_name} />
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
