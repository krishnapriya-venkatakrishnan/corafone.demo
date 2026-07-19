export type Section = "voice" | "evaluation" | "harness" | "decisions" | "playground";

const ICONS: Record<Section, string> = {
  voice: `<path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/>`,
  evaluation: `<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>`,
  harness: `<path d="M9 2v6l-6 11a2 2 0 0 0 1.8 3h14.4a2 2 0 0 0 1.8-3L15 8V2"/><path d="M9 2h6"/><path d="M8.5 13h7"/>`,
  decisions: `<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>`,
  playground: `<path d="M4 4h16v12H4z"/><path d="M4 9h16"/><path d="M9 4v5"/><circle cx="14" cy="13.5" r="1.5"/>`,
};

const LABELS: Record<Section, string> = {
  voice: "Voice Agent",
  evaluation: "Evaluation",
  harness: "Harness",
  decisions: "Decisions",
  playground: "Playground",
};

export default function Sidebar({
  active,
  onSelect,
}: {
  active: Section;
  onSelect: (section: Section) => void;
}) {
  return (
    <nav className="w-56 shrink-0 h-screen sticky top-0 flex flex-col bg-white border-r border-neutral-200 px-3 py-6">
      <div className="px-3 mb-8">
        <h1 className="text-lg font-semibold tracking-tight text-black">Corafone</h1>
        <p className="text-xs text-neutral-500">Collections dashboard</p>
      </div>

      <ul className="space-y-1">
        {(Object.keys(LABELS) as Section[]).map((section) => {
          const isActive = section === active;
          return (
            <li key={section}>
              <button
                onClick={() => onSelect(section)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? "bg-periwinkle-tint text-periwinkle"
                    : "text-neutral-600 hover:bg-neutral-100 hover:text-black"
                }`}
              >
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.75"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  className="w-4 h-4 shrink-0"
                  dangerouslySetInnerHTML={{ __html: ICONS[section] }}
                />
                {LABELS[section]}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
