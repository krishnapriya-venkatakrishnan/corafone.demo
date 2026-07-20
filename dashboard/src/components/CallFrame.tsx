import { useState } from "react";
import { FRONTEND_BASE } from "../api";

// There is one demo account for this task.
const DEMO_PHONE_NUMBER = "+15550199";

/**
 * Always rendered in the tree (see App.tsx) so React never unmounts it on
 * navigation -- switching sidebar sections only toggles `active` (CSS
 * display), never `started` or `mountKey`. The iframe's lifecycle is driven
 * exclusively by the single Start call / New call button below; navigation
 * alone never mounts, unmounts, or remounts it.
 *
 * The iframe itself only mounts once the operator clicks "Start call": its
 * src carries the demo phone number, and frontend/'s own app.js auto-starts
 * the call the instant that URL loads (the same deep-link mechanism the old
 * CallModal used), so mounting it is dialing. Framing the shot before
 * dialing (for a recording) needs that to be a deliberate click, not a side
 * effect of opening this section.
 *
 * Clicking the button while a call is live ("New call") bumps `mountKey`,
 * which forces React to tear down the current iframe. Clicking it again
 * ("Start call") then mounts a brand-new one under a new key -- a fresh
 * mount is a fresh page load of frontend/, which resets app.js's internal
 * state entirely (mic pipeline, WebSocket, timers) rather than relying on
 * its hangup path having left everything clean.
 */
export default function CallFrame({ active }: { active: boolean }) {
  const [started, setStarted] = useState(false);
  const [mountKey, setMountKey] = useState(0);

  function toggleCall() {
    if (started) {
      setStarted(false);
      setMountKey((k) => k + 1);
    } else {
      setStarted(true);
    }
  }

  return (
    <div className="h-screen w-full relative" style={{ display: active ? "block" : "none" }}>
      {started ? (
        <iframe
          key={mountKey}
          title="Voice call with Cora"
          src={`${FRONTEND_BASE}/?phone_number=${encodeURIComponent(DEMO_PHONE_NUMBER)}`}
          allow="microphone"
          className="w-full h-full border-0"
        />
      ) : (
        <div className="h-full w-full flex flex-col items-center justify-center gap-8">
          <div className="relative flex items-center justify-center w-52 h-52">
            <div className="absolute inset-0 rounded-full overflow-hidden">
              <div className="absolute inset-[-25%] idle-call-gradient" />
            </div>
          </div>
          <p className="text-xs text-neutral-500 max-w-56 text-center">
            Opens a live call with Cora as the demo account. Frame the shot first, since this dials
            the moment you click.
          </p>
        </div>
      )}

      <button
        onClick={toggleCall}
        className={
          started
            ? "absolute left-1/2 -translate-x-1/2 bottom-14 px-6 py-3 rounded-full text-sm font-medium bg-white/90 backdrop-blur border border-neutral-200 text-neutral-700 shadow-sm  hover:text-white hover:bg-periwinkle transition-colors"
            : "absolute left-1/2 -translate-x-1/2 bottom-28 px-6 py-3 rounded-full text-sm font-medium  backdrop-blur border border-neutral-200 shadow-sm bg-periwinkle hover:bg-periwinkle-soft text-white transition-colors"
        }
      >
        {started ? "New call" : "Start call"}
      </button>
    </div>
  );
}
