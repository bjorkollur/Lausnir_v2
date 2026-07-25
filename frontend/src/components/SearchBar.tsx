import { useState, useEffect, type FormEvent } from "react";
import type { SearchState } from "../lib/searchState";

export function SearchBar({ state, onChange }:
  { state: SearchState; onChange: (p: Partial<SearchState>) => void }) {
  const [text, setText] = useState(state.q);
  useEffect(() => setText(state.q), [state.q]);
  const submit = (e: FormEvent) => { e.preventDefault(); onChange({ q: text.trim() }); };
  return (
    <form onSubmit={submit} className="flex-1">
      <input
        role="searchbox"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={state.mode === "regex" ? "regex mynstur…" : "Leita…"}
        className="w-full h-12 rounded-md border border-[var(--border)] bg-[var(--surface)] px-5 text-base text-[var(--ink)] placeholder:text-[var(--ink-faint)] outline-none focus:border-[var(--accent)] transition-colors"
      />
    </form>
  );
}
