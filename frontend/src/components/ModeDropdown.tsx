import type { SearchState } from "../lib/searchState";
import type { Mode, Sort } from "../api/types";

const MODE_LABELS: Record<Mode, string> = {
  keyword: "Orðaleit",
  exact: "Heilt orð",
  prefix: "Byrjar á",
  substring: "Hluti af orði",
  any: "Eitthvað af",
  proximity: "Nálægt",
  regex: "Regex",
};

const ALL_MODES: Mode[] = [
  "keyword", "exact", "prefix", "substring", "any", "proximity", "regex",
];

// Modes that support ts_rank (can use sort=relevance)
const FTS_MODES = new Set<Mode>(["keyword", "proximity"]);

export function ModeDropdown({
  state,
  onChange,
}: {
  state: SearchState;
  onChange: (p: Partial<SearchState>) => void;
}) {
  function handleModeChange(mode: Mode) {
    // Auto-switch away from relevance when FTS rank isn't available
    const sort: Sort =
      !FTS_MODES.has(mode) && state.sort === "relevance" ? "newest" : state.sort;
    onChange({ mode, sort });
  }

  return (
    <div className="flex items-center gap-2">
      <select
        aria-label="Leitarstilling"
        value={state.mode}
        onChange={(e) => handleModeChange(e.target.value as Mode)}
        className="text-sm border border-slate-300 rounded-full px-3 py-1.5 bg-white"
      >
        {ALL_MODES.map((m) => (
          <option key={m} value={m}>
            {MODE_LABELS[m]}
          </option>
        ))}
      </select>

      {state.mode === "proximity" && (
        <label className="flex items-center gap-1 text-sm text-slate-600">
          innan
          <input
            type="number"
            min={1}
            max={50}
            value={state.proximity_n}
            onChange={(e) => {
              const n = parseInt(e.target.value, 10);
              if (Number.isFinite(n) && n >= 1 && n <= 50) onChange({ proximity_n: n });
            }}
            className="w-14 border border-slate-300 rounded px-2 py-0.5 text-center text-sm"
          />
          orða
        </label>
      )}
    </div>
  );
}
