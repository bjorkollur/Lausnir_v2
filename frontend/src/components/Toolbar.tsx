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
        className="text-sm border border-slate-300 rounded-sm px-3 py-1.5 bg-white outline-none focus:border-[#0a246a] transition-colors">
        <option value="relevance" disabled={!FTS_MODES.has(state.mode)}>
          Bestar niðurstöður
        </option>
        <option value="newest">Nýjast fyrst</option>
        <option value="oldest">Elst fyrst</option>
      </select>

      <Popover.Root>
        <Popover.Trigger className="text-sm border border-slate-300 rounded-sm px-3 py-1.5 hover:border-slate-400 transition-colors">
          Tímabil
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content className="bg-white border border-slate-200 rounded-lg p-3 shadow-md flex flex-col gap-2">
            <label className="text-sm">Frá <input type="date" value={state.date_from ?? ""}
              onChange={(e) => onChange({ date_from: e.target.value || undefined })}
              className="border rounded px-2 py-1" /></label>
            <label className="text-sm">Til <input type="date" value={state.date_to ?? ""}
              onChange={(e) => onChange({ date_to: e.target.value || undefined })}
              className="border rounded px-2 py-1" /></label>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>

      <ProvisionInput value={state.provision ?? ""} onChange={(v) => onChange({ provision: v || undefined })} />

      {REGEX_BACKED_MODES.has(state.mode) && (
        <Popover.Root>
          <Popover.Trigger className="text-sm border border-slate-300 rounded-sm px-3 py-1.5 hover:border-slate-400 transition-colors">
            Reitir
          </Popover.Trigger>
          <Popover.Portal>
            <Popover.Content className="bg-white border border-slate-200 rounded-lg p-3 shadow-md flex flex-col gap-1">
              {regexFields.map((f) => {
                const base = state.regex_fields.length ? state.regex_fields : ["body_text"];
                const on = base.includes(f);
                return (
                  <label key={f} className="text-sm flex items-center gap-2">
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

function ProvisionInput({ value, onChange }: { value: string; onChange: (v: string) => void }) {
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
          className={`text-sm border rounded-sm px-3 py-1.5 w-44 outline-none transition-colors ${
            value ? "border-indigo-400 bg-indigo-50" : "border-slate-300 bg-white hover:border-slate-400"
          } focus:border-[#0a246a]`}
        />
        {draft && (
          <button
            type="button"
            onClick={clear}
            aria-label="Hreinsa lagaákvæði"
            className="absolute right-1.5 text-slate-400 hover:text-slate-600 text-xs leading-none"
          >
            ✕
          </button>
        )}
      </div>
      {draft !== value && (
        <button
          type="submit"
          className="text-xs text-indigo-600 hover:text-indigo-800 px-1"
        >
          Leita
        </button>
      )}
    </form>
  );
}
