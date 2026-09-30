import { useId, useRef, useState, type FormEvent } from "react";
import { CaretRightIcon, MagnifyingGlassIcon } from "@phosphor-icons/react";
import type { CatalogNode, Mode } from "../api/types";
import { activeFilterCount, hasSearchCriteria, type SearchState } from "../lib/searchState";
import { ALL_MODES, MODE_HINTS, MODE_LABELS, sortForMode } from "../lib/modes";
import { SourceTree } from "./SourceTree";
import { formatCount } from "../lib/formatNumber";

const ADVANCED_KEY = "lausnir-advanced-open";

function readAdvancedOpen(): boolean {
  try {
    return localStorage.getItem(ADVANCED_KEY) === "1";
  } catch {
    return false;
  }
}

const LEGEND = "mb-2.5 text-micro font-medium uppercase tracking-[0.1em] text-ink-faint";
const FIELD =
  "h-10 w-full min-w-0 rounded-md border border-border bg-surface px-3 text-meta text-ink placeholder:text-ink-faint transition-colors hover:border-border-strong";

/** The front page: one field and a button, and advanced search one click
 *  away rather than a second page of controls under the first.
 *
 *  The whole page is one form and nothing is applied until it is submitted.
 *  Each advanced control used to write itself into the URL the moment it
 *  changed, so ticking a source (which makes the URL a search) swapped this page
 *  for the results mid-form and threw away the query typed above it. */
export function LandingView({
  state,
  catalog,
  total,
  sourceCount,
  patch,
}: {
  state: SearchState;
  catalog: CatalogNode[];
  total: number;
  sourceCount: number;
  patch: (p: Partial<SearchState>) => void;
}) {
  const [draft, setDraft] = useState<SearchState>(state);
  const set = (p: Partial<SearchState>) => setDraft((d) => ({ ...d, ...p }));
  const advancedCount =
    activeFilterCount(draft) + (draft.mode !== "keyword" ? 1 : 0);
  const [open, setOpen] = useState(() => readAdvancedOpen() || advancedCount > 0);
  const input = useRef<HTMLInputElement>(null);
  const ids = { q: useId(), panel: useId(), provision: useId(), keyword: useId(), from: useId(), to: useId() };

  const toggle = () => {
    setOpen((o) => {
      try {
        localStorage.setItem(ADVANCED_KEY, o ? "0" : "1");
      } catch {
        /* private mode: the panel still toggles for this visit */
      }
      return !o;
    });
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const next: SearchState = {
      ...draft,
      q: draft.q.trim(),
      provision: draft.provision?.trim() || undefined,
      keyword: draft.keyword?.trim() || undefined,
    };
    if (!hasSearchCriteria(next)) {
      input.current?.focus();
      return;
    }
    patch(next);
  };

  const reset = () =>
    set({
      mode: "keyword", sort: "relevance", scope: [], date_from: undefined, date_to: undefined,
      provision: undefined, keyword: undefined, proximity_n: 5,
    });

  return (
    <div className="flex min-h-full flex-col items-center px-4 pb-24 sm:px-6">
      <form onSubmit={submit} role="search" aria-label="Leit" className="w-full max-w-2xl pt-[12vh] sm:pt-[16vh]">
        <div className="mb-9 text-center">
          <h1 className="font-serif text-6xl font-medium tracking-[-0.02em] text-ink">Lausnir</h1>
          <p className="mt-3 text-body text-ink-soft">Íslenskar réttarheimildir</p>
        </div>

        <div className="flex gap-2">
          <label htmlFor={ids.q} className="sr-only">Leitarorð</label>
          <div className="relative min-w-0 flex-1">
            <MagnifyingGlassIcon
              size={18}
              aria-hidden
              className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-faint"
            />
            <input
              id={ids.q}
              ref={input}
              role="searchbox"
              autoFocus
              enterKeyHint="search"
              value={draft.q}
              onChange={(e) => set({ q: e.target.value })}
              placeholder={draft.mode === "regex" ? "regex mynstur…" : "Leita í réttarheimildum…"}
              className="h-12 w-full rounded-md border border-border bg-surface pl-11 pr-4 text-body text-ink placeholder:text-ink-faint transition-colors hover:border-border-strong"
            />
          </div>
          <button
            type="submit"
            className="h-12 shrink-0 rounded-md bg-cta px-6 font-medium text-cta-ink transition-colors hover:bg-cta-hover"
          >
            Leita
          </button>
        </div>

        <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <button
            type="button"
            onClick={toggle}
            aria-expanded={open}
            aria-controls={ids.panel}
            className="-ml-1 inline-flex h-8 items-center gap-1.5 rounded px-1 text-meta text-ink-soft hover:text-ink"
          >
            <CaretRightIcon
              size={12}
              weight="bold"
              aria-hidden
              className={`transition-transform duration-150 ${open ? "rotate-90" : ""}`}
            />
            Ýtarleg leit
            {advancedCount > 0 && (
              <span className="tabular grid h-5 min-w-5 place-items-center rounded-full bg-accent-soft px-1.5 text-micro text-ink">
                {advancedCount}
              </span>
            )}
          </button>
          {total > 0 && (
            <p className="tabular text-meta text-ink-faint">
              {formatCount(total)} skjöl · {sourceCount} heimildir
            </p>
          )}
        </div>

        <div id={ids.panel} hidden={!open} className="mt-5 space-y-8 border-t border-border pt-7">
          <fieldset>
            <legend className={LEGEND}>Leitarhamur</legend>
            <div className="flex flex-wrap gap-1.5">
              {ALL_MODES.map((m) => (
                <label
                  key={m}
                  className={`relative inline-flex h-9 cursor-pointer items-center rounded-md border px-3.5 text-meta transition-colors has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-1 has-[:focus-visible]:outline-ring ${
                    draft.mode === m
                      ? "border-accent bg-accent-soft text-ink"
                      : "border-border bg-surface text-ink-soft hover:border-border-strong hover:text-ink"
                  }`}
                >
                  <input
                    type="radio"
                    name="mode"
                    value={m}
                    checked={draft.mode === m}
                    onChange={() => set({ mode: m as Mode, sort: sortForMode(m, draft.sort) })}
                    className="sr-only"
                  />
                  {MODE_LABELS[m]}
                </label>
              ))}
            </div>
            <p className="mt-2.5 text-meta text-ink-soft">
              {MODE_HINTS[draft.mode]}
              {draft.mode === "proximity" && (
                <label className="ml-2 inline-flex items-center gap-1.5">
                  Innan
                  <input
                    type="number"
                    min={1}
                    max={50}
                    value={draft.proximity_n}
                    onChange={(e) => {
                      const n = parseInt(e.target.value, 10);
                      if (Number.isFinite(n) && n >= 1 && n <= 50) set({ proximity_n: n });
                    }}
                    className="h-8 w-14 rounded-md border border-border bg-surface px-2 text-center text-meta text-ink"
                  />
                  orða
                </label>
              )}
            </p>
          </fieldset>

          <div className="grid gap-5 sm:grid-cols-2">
            <div>
              <label htmlFor={ids.provision} className={LEGEND + " block"}>Lagaákvæði</label>
              <input
                id={ids.provision}
                value={draft.provision ?? ""}
                onChange={(e) => set({ provision: e.target.value })}
                placeholder="t.d. 72. gr. laga nr. 33/1944"
                className={FIELD}
              />
            </div>
            <div>
              <label htmlFor={ids.keyword} className={LEGEND + " block"}>Lykilorð</label>
              <input
                id={ids.keyword}
                value={draft.keyword ?? ""}
                onChange={(e) => set({ keyword: e.target.value })}
                placeholder="t.d. Gæsluvarðhald"
                className={FIELD}
              />
            </div>
          </div>

          <fieldset>
            <legend className={LEGEND}>Tímabil</legend>
            <div className="grid grid-cols-2 gap-3 sm:max-w-sm">
              <div>
                <label htmlFor={ids.from} className="mb-1 block text-micro text-ink-soft">Frá</label>
                <input
                  id={ids.from}
                  type="date"
                  value={draft.date_from ?? ""}
                  onChange={(e) => set({ date_from: e.target.value || undefined })}
                  className={FIELD}
                />
              </div>
              <div>
                <label htmlFor={ids.to} className="mb-1 block text-micro text-ink-soft">Til</label>
                <input
                  id={ids.to}
                  type="date"
                  value={draft.date_to ?? ""}
                  onChange={(e) => set({ date_to: e.target.value || undefined })}
                  className={FIELD}
                />
              </div>
            </div>
          </fieldset>

          <fieldset>
            <legend className={LEGEND}>
              Heimildir
              {draft.scope.length > 0 && (
                <span className="ml-2 font-normal normal-case tracking-normal text-accent">
                  {draft.scope.length} {draft.scope.length === 1 ? "valin" : "valdar"}
                </span>
              )}
            </legend>
            <SourceTree catalog={catalog} scope={draft.scope} onScopeChange={(scope) => set({ scope })} />
          </fieldset>

          <div className="flex flex-wrap items-center gap-3 border-t border-border pt-6">
            <button
              type="submit"
              className="h-11 rounded-md bg-cta px-6 font-medium text-cta-ink transition-colors hover:bg-cta-hover"
            >
              Leita
            </button>
            {advancedCount > 0 && (
              <button
                type="button"
                onClick={reset}
                className="h-11 rounded-md px-3 text-meta text-ink-soft underline-offset-2 hover:text-ink hover:underline"
              >
                Hreinsa ýtarlega leit
              </button>
            )}
          </div>
        </div>
      </form>
    </div>
  );
}
