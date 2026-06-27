import * as Switch from "@radix-ui/react-switch";
import type { SearchState } from "../lib/searchState";

export function RegexToggle({ state, onChange }:
  { state: SearchState; onChange: (p: Partial<SearchState>) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm text-slate-600">
      <Switch.Root
        checked={state.mode === "regex"}
        onCheckedChange={(on) =>
          onChange({ mode: on ? "regex" : "keyword", sort: on ? "newest" : "relevance" })}
        className="w-10 h-6 rounded-full bg-slate-300 data-[state=checked]:bg-indigo-600 relative">
        <Switch.Thumb className="block w-5 h-5 bg-white rounded-full translate-x-0.5 data-[state=checked]:translate-x-[18px] transition-transform" />
      </Switch.Root>
      Regex
    </label>
  );
}
