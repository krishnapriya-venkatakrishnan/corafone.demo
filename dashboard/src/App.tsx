import { useState } from "react";
import Sidebar, { type Section } from "./components/Sidebar";
import CallFrame from "./components/CallFrame";
import CallReportPanel from "./components/CallReportPanel";
import DecisionsPanel from "./components/DecisionsPanel";
import PlaygroundPanel from "./components/PlaygroundPanel";

const TITLES: Record<Section, string> = {
  voice: "Voice Agent",
  callReport: "Call Report",
  decisions: "Decisions",
  playground: "Playground",
};

export default function App() {
  const [active, setActive] = useState<Section>("voice");

  return (
    <div className="min-h-screen bg-white text-black font-sans antialiased flex">
      <Sidebar active={active} onSelect={setActive} />

      <main className="flex-1 min-w-0">
        {/* Always mounted -- never conditionally rendered -- so the call's
            WebSocket survives navigating to another section. See CallFrame. */}
        <CallFrame active={active === "voice"} />

        {active !== "voice" && (
          <div
            className={
              active === "callReport"
                ? "px-4 py-6 md:px-10 md:py-10"
                : "max-w-5xl mx-auto px-4 py-6 md:px-10 md:py-10"
            }
          >
            <h2 className="text-xl font-semibold text-black mb-6">{TITLES[active]}</h2>
            {active === "callReport" && <CallReportPanel />}
            {active === "decisions" && <DecisionsPanel />}
            {active === "playground" && <PlaygroundPanel />}
          </div>
        )}
      </main>
    </div>
  );
}
