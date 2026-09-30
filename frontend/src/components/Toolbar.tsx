import * as Popover from "@radix-ui/react-popover";
import { CaretDownIcon } from "@phosphor-icons/react";
import type { SearchState } from "../lib/searchState";
import type { Sort } from "../api/types";
import { FTS_MODES, effectiveSort } from "../lib/modes";

const REGEX_FIELD_LABELS: Record<string, string> = {
  body_text: "Meginmál", summary: "Reifun", case_number: "Málsnúmer",
  parties: "Aðilar", keywords: "Lykilorð", lower_body_text: "Neðri dómur",
};

/** Shared by every control in the search header, so the row reads as one
 *  height, one border and one radius. */
export const CONTROL =
  "h-9 rounded-md border border-border bg-surface px-2.5 text-meta text-ink transition-colors hover:border-border-strong";

export function SortSelect({ state, onChange, className = "" }:
  { state: SearchState; onChange: (p: Partial<SearchState>) => void; className?: string }) {
  const canRank = Boolean(state.q) && FTS_MODES.has(state.mode);
  return (
    <select
      aria-label="Röðun"
      value={effectiveSort(state)}
      onChange={(e) => onChange({ sort: e.target.value as Sort })}
      className={`${CONTROL} ${className}`}
    >
      <option value="relevance" disabled={!canRank}>Bestar niðurstöður</option>
      <option value="newest">Nýjast fyrst</option>
      <option value="oldest">Elst fyrst</option>
    </select>
  );
}

/** Which columns a regex-backed mode searches. Only shown for those modes. */
export function FieldsPicker({ state, regexFields, onChange }:
  { state: SearchState; regexFields: string[]; onChange: (p: Partial<SearchState>) => void }) {
  const base = state.regex_fields.length ? state.regex_fields : ["body_text"];
  return (
    <Popover.Root>
      <Popover.Trigger className={`${CONTROL} inline-flex shrink-0 items-center gap-1.5 text-ink-soft hover:text-ink`}>
        Reitir
        <span className="tabular text-ink-faint">{base.length}</span>
        <CaretDownIcon size={11} aria-hidden />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          sideOffset={6}
          align="start"
          className="z-50 flex min-w-44 flex-col gap-0.5 rounded-md border border-border bg-surface p-1.5 shadow-pop"
        >
          <p className="px-2 pb-1 pt-0.5 text-micro text-ink-faint">Leita í</p>
          {regexFields.map((f) => {
            const on = base.includes(f);
            return (
              <label
                key={f}
                className="flex cursor-pointer items-center gap-2.5 rounded px-2 py-1.5 text-meta text-ink hover:bg-surface-sunken"
              >
                <input
                  type="checkbox"
                  checked={on}
                  onChange={(e) => {
                    const next = e.target.checked
                      ? [...new Set([...base, f])]
                      : base.filter((x) => x !== f);
                    onChange({ regex_fields: next });
                  }}
                />
                {REGEX_FIELD_LABELS[f] ?? f}
              </label>
            );
          })}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
