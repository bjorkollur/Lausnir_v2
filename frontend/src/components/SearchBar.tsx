import { useState, useEffect, FormEvent } from "react";
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
        className="w-full h-12 rounded-full border border-slate-300 px-5 text-base outline-none focus:border-indigo-500"
      />
    </form>
  );
}
