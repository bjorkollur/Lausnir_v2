import * as Popover from "@radix-ui/react-popover";
import { QuestionIcon } from "@phosphor-icons/react";
import type { SearchState } from "../lib/searchState";
import type { Mode } from "../api/types";
import { ALL_MODES, MODE_HINTS, MODE_LABELS, sortForMode } from "../lib/modes";
import { CONTROL } from "./Toolbar";

export function ModeDropdown({
  state,
  onChange,
}: {
  state: SearchState;
  onChange: (p: Partial<SearchState>) => void;
}) {
  function handleModeChange(mode: Mode) {
    // Auto-switch away from relevance when FTS rank isn't available
    onChange({ mode, sort: sortForMode(mode, state.sort) });
  }

  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <select
        aria-label="Leitarstilling"
        value={state.mode}
        onChange={(e) => handleModeChange(e.target.value as Mode)}
        title={MODE_HINTS[state.mode]}
        className={`${CONTROL} min-w-0`}
      >
        {ALL_MODES.map((m) => (
          <option key={m} value={m} title={MODE_HINTS[m]}>
            {MODE_LABELS[m]}
          </option>
        ))}
      </select>

      {/* Seven modes with names like "Byrjar á" and "Hluti af orði" are not
          self-explanatory, and a native select cannot carry a description per
          option. One click shows what each of them matches. */}
      <Popover.Root>
        {/* Not on a phone: mode, sort and "Síur" need the whole row there, and
            the front page's advanced search describes every mode anyway. */}
        <Popover.Trigger
          aria-label="Um leitarhami"
          className="hidden h-9 w-7 shrink-0 place-items-center rounded-md text-ink-faint transition-colors hover:text-ink sm:grid"
        >
          <QuestionIcon size={16} aria-hidden />
        </Popover.Trigger>
        <Popover.Portal>
          <Popover.Content
            sideOffset={6}
            align="start"
            collisionPadding={12}
            className="z-50 w-[min(22rem,calc(100vw-24px))] rounded-md border border-border bg-surface p-4 shadow-pop"
          >
            <p className="mb-3 text-meta font-medium text-ink">Leitarhamir</p>
            <dl className="space-y-2 text-meta">
              {ALL_MODES.map((m) => (
                <div key={m} className="grid grid-cols-[6.5rem_1fr] gap-3">
                  <dt className={m === state.mode ? "font-medium text-ink" : "text-ink"}>
                    {MODE_LABELS[m]}
                  </dt>
                  <dd className="text-ink-soft">{MODE_HINTS[m]}</dd>
                </div>
              ))}
            </dl>
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>

      {state.mode === "proximity" && (
        <label className="flex shrink-0 items-center gap-1.5 text-meta text-ink-soft">
          innan
          <input
            type="number"
            min={1}
            max={50}
            value={state.proximity_n}
            aria-label="Hámarksfjarlægð í orðum"
            onChange={(e) => {
              const n = parseInt(e.target.value, 10);
              if (Number.isFinite(n) && n >= 1 && n <= 50) onChange({ proximity_n: n });
            }}
            className={`${CONTROL} w-14 px-2 text-center`}
          />
          orða
        </label>
      )}
    </div>
  );
}
