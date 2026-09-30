import { useState, useEffect, useRef, type FormEvent } from "react";
import { MagnifyingGlassIcon, XIcon } from "@phosphor-icons/react";
import type { SearchState } from "../lib/searchState";

/** The query field in the results header, as a search landmark with a visible
 *  submit: GOV.UK's search pattern, a field with its button attached. Enter was
 *  the only way to submit, and nothing on screen said so. */
export function SearchBar({ state, onChange }:
  { state: SearchState; onChange: (p: Partial<SearchState>) => void }) {
  const [text, setText] = useState(state.q);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => setText(state.q), [state.q]);
  const submit = (e: FormEvent) => { e.preventDefault(); onChange({ q: text.trim() }); };

  return (
    <form
      role="search"
      aria-label="Leit"
      onSubmit={submit}
      className="flex min-w-0 flex-1 basis-full sm:max-w-xl sm:basis-auto"
    >
      <div className="relative min-w-0 flex-1">
        <input
          ref={input}
          role="searchbox"
          aria-label="Leitarorð"
          enterKeyHint="search"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={state.mode === "regex" ? "regex mynstur…" : "Leita…"}
          className="h-9 w-full rounded-l-md border border-border bg-surface pl-3 pr-8 text-meta text-ink placeholder:text-ink-faint transition-colors hover:border-border-strong"
        />
        {text && (
          <button
            type="button"
            aria-label="Hreinsa leitarorð"
            onClick={() => {
              setText("");
              input.current?.focus();
            }}
            className="absolute right-1 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded text-ink-faint hover:text-ink"
          >
            <XIcon size={13} aria-hidden />
          </button>
        )}
      </div>
      <button
        type="submit"
        aria-label="Leita"
        className="grid h-9 w-10 shrink-0 place-items-center rounded-r-md bg-cta text-cta-ink transition-colors hover:bg-cta-hover"
      >
        <MagnifyingGlassIcon size={16} weight="bold" aria-hidden />
      </button>
    </form>
  );
}
