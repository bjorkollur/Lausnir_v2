import * as Popover from "@radix-ui/react-popover";
import type { SearchState } from "../lib/searchState";
import type { Mode, Sort } from "../api/types";

const REGEX_FIELD_LABELS: Record<string, string> = {
  body_text: "Meginmál", summary: "Reifun", case_number: "Málsnúmer",
  parties: "Aðilar", keywords: "Lykilorð", lower_body_text: "Neðri dómur",
};

// Modes that use regex_fields (show Reitir button)
const REGEX_BACKED_MODES = new Set<Mode>(["exact", "prefix", "substring", "any", "regex"]);
// Modes where relevance sort is meaningful
const FTS_MODES = new Set<Mode>(["keyword", "proximity"]);

export function Toolbar({ state, regexFields, onChange }:
  { state: SearchState; regexFields: string[]; onChange: (p: Partial<SearchState>) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <select
        aria-label="Röðun"
        value={state.sort}
        onChange={(e) => onChange({ sort: e.target.value as Sort })}
        className="text-sm border border-[var(--border)] rounded-md px-3 py-1.5 bg-[var(--surface)] text-[var(--ink)] outline-none focus:border-[var(--accent)] transition-colors">
        <option value="relevance" disabled={!FTS_MODES.has(state.mode)}>
          Bestar niðurstöður
        </option>
        <option value="newest">Nýjast fyrst</option>
        <option value="oldest">Elst fyrst</option>
      </select>

      <Popover.Root>
        <Popover.Trigger className="text-sm border border-[var(--border)] rounded-md px-3 py-1.5 bg-[var(--surface)] text-[var(--ink-soft)] hover:border-[var(--border-strong)] hover:text-[var(--ink)] transition-colors">
          Tímabil
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content sideOffset={6} className="bg-[var(--surface)] border border-[var(--border)] rounded-md p-3 flex flex-col gap-2 z-50">
            <label className="text-sm text-[var(--ink-soft)] flex items-center gap-2">
              <span className="w-7">Frá</span>
              <input type="date" value={state.date_from ?? ""}
                onChange={(e) => onChange({ date_from: e.target.value || undefined })}
                className="border border-[var(--border)] bg-[var(--surface)] rounded-md px-2 py-1 text-[var(--ink)] outline-none focus:border-[var(--accent)] transition-colors" />
            </label>
            <label className="text-sm text-[var(--ink-soft)] flex items-center gap-2">
              <span className="w-7">Til</span>
              <input type="date" value={state.date_to ?? ""}
                onChange={(e) => onChange({ date_to: e.target.value || undefined })}
                className="border border-[var(--border)] bg-[var(--surface)] rounded-md px-2 py-1 text-[var(--ink)] outline-none focus:border-[var(--accent)] transition-colors" />
            </label>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>

      <ProvisionInput value={state.provision ?? ""} onChange={(v) => onChange({ provision: v || undefined })} />

      <KeywordInput value={state.keyword ?? ""} onChange={(v) => onChange({ keyword: v || undefined })} />

      {REGEX_BACKED_MODES.has(state.mode) && (
        <Popover.Root>
          <Popover.Trigger className="text-sm border border-[var(--border)] rounded-md px-3 py-1.5 bg-[var(--surface)] text-[var(--ink-soft)] hover:border-[var(--border-strong)] hover:text-[var(--ink)] transition-colors">
            Reitir
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content sideOffset={6} className="bg-[var(--surface)] border border-[var(--border)] rounded-md p-3 flex flex-col gap-1 z-50">
              {regexFields.map((f) => {
                const base = state.regex_fields.length ? state.regex_fields : ["body_text"];
                const on = base.includes(f);
                return (
                  <label key={f} className="text-sm text-[var(--ink-soft)] flex items-center gap-2 accent-[var(--accent)]">
                    <input type="checkbox" checked={on} onChange={(e) => {
                      const next = e.target.checked
                        ? [...new Set([...base, f])]
                        : base.filter((x) => x !== f);
                      onChange({ regex_fields: next });
                    }} />
                    {REGEX_FIELD_LABELS[f] ?? f}
                  </label>
                );
              })}
            </Popover.Content>
          </Popover.Portal>
        </Popover.Root>
      )}
    </div>
  );
}

// ── ProvisionInput ────────────────────────────────────────────────────────────

import { useState, useEffect, type FormEvent } from "react";

export function ProvisionInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);

  const submit = (e: FormEvent) => { e.preventDefault(); onChange(draft.trim()); };
  const clear = () => { setDraft(""); onChange(""); };

  return (
    <form onSubmit={submit} className="flex items-center gap-1">
      <div className="relative flex items-center">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Lagaákvæði…"
          aria-label="Leita eftir lagaákvæði"
          className={`text-sm border rounded-md px-3 py-1.5 w-44 text-[var(--ink)] placeholder:text-[var(--ink-faint)] outline-none transition-colors ${
            value ? "border-[var(--accent)] bg-[var(--accent-soft)]" : "border-[var(--border)] bg-[var(--surface)] hover:border-[var(--border-strong)]"
          } focus:border-[var(--accent)]`}
        />
        {draft && (
          <button
            type="button"
            onClick={clear}
            aria-label="Hreinsa lagaákvæði"
            className="absolute right-1.5 text-[var(--ink-faint)] hover:text-[var(--ink)] text-xs leading-none"
          >
            ✕
          </button>
        )}
      </div>
      {draft !== value && (
        <button
          type="submit"
          className="text-xs text-[var(--accent)] hover:text-[var(--ink)] px-1"
        >
          Leita
        </button>
      )}
    </form>
  );
}

// ── KeywordInput ──────────────────────────────────────────────────────────────

export function KeywordInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);

  const submit = (e: FormEvent) => { e.preventDefault(); onChange(draft.trim()); };
  const clear = () => { setDraft(""); onChange(""); };

  return (
    <form onSubmit={submit} className="flex items-center gap-1">
      <div className="relative flex items-center">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Lykilorð…"
          aria-label="Leita eftir lykilorði"
          className={`text-sm border rounded-md px-3 py-1.5 w-44 text-[var(--ink)] placeholder:text-[var(--ink-faint)] outline-none transition-colors ${
            value ? "border-[var(--accent)] bg-[var(--accent-soft)]" : "border-[var(--border)] bg-[var(--surface)] hover:border-[var(--border-strong)]"
          } focus:border-[var(--accent)]`}
        />
        {draft && (
          <button
            type="button"
            onClick={clear}
            aria-label="Hreinsa lykilorð"
            className="absolute right-1.5 text-[var(--ink-faint)] hover:text-[var(--ink)] text-xs leading-none"
          >
            ✕
          </button>
        )}
      </div>
      {draft !== value && (
        <button
          type="submit"
          className="text-xs text-[var(--accent)] hover:text-[var(--ink)] px-1"
        >
          Leita
        </button>
      )}
    </form>
  );
}
