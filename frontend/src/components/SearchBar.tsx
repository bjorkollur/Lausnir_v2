import { useState, useEffect, type FormEvent } from "react";
import type { SearchState } from "../lib/searchState";

export function SearchBar({ state, onChange }:
  { state: SearchState; onChange: (p: Partial<SearchState>) => void }) {
  const [text, setText] = useState(state.q);
  useEffect(() => setText(state.q), [state.q]);
  const submit = (e: FormEvent) => { e.preventDefault(); onChange({ q: text.trim() }); };
  return (
    <form onSubmit={submit} className="min-w-0 flex-1 basis-full sm:basis-auto">
      <input
        role="searchbox"
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder={state.mode === "regex" ? "regex mynstur…" : "Leita…"}
        className="h-9 w-full rounded-md border border-border bg-surface px-3 text-meta text-ink placeholder:text-ink-faint transition-colors hover:border-border-strong"
      />
    </form>
  );
}
