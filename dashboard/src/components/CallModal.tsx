import type { AccountSummary } from "../types";
import { FRONTEND_BASE } from "../api";

interface CallModalProps {
  account: AccountSummary;
  onClose: () => void;
}

export default function CallModal({ account, onClose }: CallModalProps) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-neutral-950/80 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="w-full max-w-sm rounded-2xl bg-neutral-900 border border-neutral-800 overflow-hidden shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-neutral-800">
          <p className="text-sm text-neutral-300">
            Calling <span className="text-neutral-50 font-medium">{account.customer_name}</span>
          </p>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-neutral-500 hover:text-neutral-200 transition-colors text-sm"
          >
            ✕
          </button>
        </div>
        <iframe
          title={`Call ${account.customer_name}`}
          src={`${FRONTEND_BASE}/?phone_number=${encodeURIComponent(account.phone_number)}`}
          allow="microphone; autoplay"
          className="w-full h-[640px] bg-neutral-950"
        />
      </div>
    </div>
  );
}
