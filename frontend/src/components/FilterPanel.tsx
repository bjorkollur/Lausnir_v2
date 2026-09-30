import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { ArrowRightIcon, XIcon } from "@phosphor-icons/react";
import { useFacets } from "../hooks/useFacets";
import { useSearch } from "../hooks/useSearch";
import { FacetNode } from "./FacetNode";
import { toggleScope } from "../lib/scopeTree";
import { activeFilterCount, CLEAR_FILTERS, type SearchState } from "../lib/searchState";
import { formatCount } from "../lib/formatNumber";

const SECTION_HEADING = "mb-2 text-micro font-medium uppercase tracking-[0.1em] text-ink-faint";
const FIELD =
  "h-9 w-full min-w-0 rounded-md border border-border bg-surface px-2.5 text-meta text-ink placeholder:text-ink-faint transition-colors hover:border-border-strong";

/** A native date field reports a value as soon as every segment holds a digit,
 *  so typing "2020" into the year used to run four searches, for the years 2,
 *  20, 202 and 2020. Only a plausible year is committed. */
function DateField({ label, value, onCommit }:
  { label: string; value: string | undefined; onCommit: (v: string | undefined) => void }) {
  const id = useId();
  const [draft, setDraft] = useState(value ?? "");
  useEffect(() => setDraft(value ?? ""), [value]);
  const commit = (v: string) => {
    if (v === "") onCommit(undefined);
    else if (Number(v.slice(0, 4)) >= 1800 && v !== value) onCommit(v);
  };
  return (
    <div className="min-w-0 flex-1">
      <label htmlFor={id} className="mb-1 block text-micro text-ink-soft">{label}</label>
      <input
        id={id}
        type="date"
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          commit(e.target.value);
        }}
        className={`${FIELD} px-2`}
      />
    </div>
  );
}

/** A text filter applied with Enter, or with the button that appears once the
 *  field differs from the filter in force. Applying on every keystroke would
 *  run a search per letter of "72. gr. laga nr. 33/1944". */
function TextFilter({ label, placeholder, value, onCommit }:
  { label: string; placeholder: string; value: string; onCommit: (v: string) => void }) {
  const id = useId();
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const dirty = draft.trim() !== value;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onCommit(draft.trim());
  };
  return (
    <form onSubmit={submit}>
      <label htmlFor={id} className={SECTION_HEADING + " block"}>{label}</label>
      <div className="flex gap-1.5">
        <div className="relative min-w-0 flex-1">
          <input
            id={id}
            type="text"
            value={draft}
            enterKeyHint="search"
            onChange={(e) => setDraft(e.target.value)}
            placeholder={placeholder}
            className={`${FIELD} pr-8`}
          />
          {draft && (
            <button
              type="button"
              aria-label={`Hreinsa ${label.toLowerCase()}`}
              onClick={() => {
                setDraft("");
                if (value) onCommit("");
              }}
              className="absolute right-1 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded text-ink-faint hover:text-ink"
            >
              <XIcon size={12} aria-hidden />
            </button>
          )}
        </div>
        {dirty && (
          <button
            type="submit"
            aria-label={`Nota ${label.toLowerCase()}`}
            className="grid h-9 w-9 shrink-0 place-items-center rounded-md bg-cta text-cta-ink hover:bg-cta-hover"
          >
            <ArrowRightIcon size={14} weight="bold" aria-hidden />
          </button>
        )}
      </div>
    </form>
  );
}

/** Below lg the footer of the off-canvas panel says what closing it will show.
 *  Shares the results list's query key, so it costs no request of its own. */
function ShowResultsButton({ state, onClose }: { state: SearchState; onClose?: () => void }) {
  const q = useSearch(state);
  const total = q.data?.pages[0]?.total;
  return (
    <button
      type="button"
      onClick={onClose}
      className="h-11 w-full rounded-md bg-cta text-meta font-medium text-cta-ink hover:bg-cta-hover"
    >
      {total === undefined
        ? "Sýna niðurstöður"
        : total === 1
          ? "Sýna 1 niðurstöðu"
          : `Sýna ${formatCount(total)} niðurstöður`}
    </button>
  );
}

/** Every way to narrow a search, in one labelled column: period, provision,
 *  keyword and sources. The period used to hide in a popover whose trigger read
 *  "Tímabil" whether or not a period was in force, and provision and keyword
 *  were two unlabelled fields named only by their placeholders.
 *
 *  Left of the results, where search filters conventionally sit, and the same
 *  element below lg, where it slides in as a modal panel. */
export function FilterPanel({ state, onChange, open = false, onClose }: {
  state: SearchState;
  onChange: (p: Partial<SearchState>) => void;
  /** Below lg the panel slides in; above it, it is always the static column. */
  open?: boolean;
  onClose?: () => void;
}) {
  const { data, isPending } = useFacets(state);
  const selected = new Set(state.scope);
  const catalog = data?.catalog ?? [];
  const visibleGroups = catalog.filter((node) => node.count > 0 || selected.has(node.key));
  const panel = useRef<HTMLElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const filterCount = activeFilterCount(state);

  // Modal behaviour only while open below lg: focus moves in and stays in,
  // Escape closes, and focus goes back to whatever opened the panel. onClose is
  // read through a ref: the page passes a fresh arrow each render, and an effect
  // keyed on it would bounce focus to the close button after every facet click.
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    closeButton.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closeRef.current?.();
        return;
      }
      if (e.key !== "Tab" || !panel.current) return;
      const focusable = Array.from(
        panel.current.querySelectorAll<HTMLElement>("button, input, select, a[href]"),
      ).filter((el) => !el.hasAttribute("disabled") && el.offsetParent !== null);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      opener?.focus?.();
    };
  }, [open]);

  return (
    <>
      {open && (
        <div
          aria-hidden
          onClick={onClose}
          className="fixed inset-0 z-40 bg-ink/25 lg:hidden"
        />
      )}
      <aside
        ref={panel}
        aria-label="Síur"
        {...(open ? { role: "dialog", "aria-modal": true } : {})}
        // Closed below lg the panel is parked off-screen; `invisible` keeps its
        // controls out of the tab order too, or a keyboard user would tab into
        // a panel they cannot see.
        // The transition list differs by direction: opening turns visibility on
        // at once, so the close button can take focus in the same frame;
        // closing keeps it on until the slide has finished.
        className={`fixed inset-y-0 left-0 z-50 flex w-[85vw] max-w-[340px] flex-col border-r border-border bg-surface shadow-pop duration-200 lg:visible lg:static lg:z-auto lg:w-[272px] lg:max-w-none lg:shrink-0 lg:translate-x-0 lg:bg-transparent lg:shadow-none lg:transition-none ${
          open ? "visible translate-x-0 transition-transform" : "invisible -translate-x-full transition-[transform,visibility]"
        }`}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3 lg:hidden">
          <h2 className="text-heading text-ink">Síur</h2>
          <button
            ref={closeButton}
            type="button"
            onClick={onClose}
            aria-label="Loka síum"
            className="grid h-9 w-9 place-items-center rounded-md text-ink-soft hover:bg-surface-sunken hover:text-ink"
          >
            <XIcon size={16} aria-hidden />
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-4 py-5 lg:pl-0 lg:pr-6">
          <div className="hidden items-baseline justify-between lg:flex">
            <h2 className="text-meta font-medium text-ink">Síur</h2>
            {filterCount > 0 && (
              <button
                type="button"
                onClick={() => onChange(CLEAR_FILTERS)}
                className="text-micro text-ink-soft underline-offset-2 hover:text-ink hover:underline"
              >
                Hreinsa allt
              </button>
            )}
          </div>

          <section>
            <h3 className={SECTION_HEADING}>Tímabil</h3>
            <div className="flex gap-2">
              <DateField
                label="Frá"
                value={state.date_from}
                onCommit={(v) => onChange({ date_from: v })}
              />
              <DateField
                label="Til"
                value={state.date_to}
                onCommit={(v) => onChange({ date_to: v })}
              />
            </div>
          </section>

          <TextFilter
            label="Lagaákvæði"
            placeholder="t.d. 72. gr. laga nr. 33/1944"
            value={state.provision ?? ""}
            onCommit={(v) => onChange({ provision: v || undefined })}
          />

          <TextFilter
            label="Lykilorð"
            placeholder="t.d. Gæsluvarðhald"
            value={state.keyword ?? ""}
            onCommit={(v) => onChange({ keyword: v || undefined })}
          />

          <section>
            <h3 className={SECTION_HEADING}>Heimildir</h3>
            {isPending && <div className="h-40 animate-pulse rounded bg-surface-sunken" />}
            {!isPending && visibleGroups.length === 0 && (
              <p className="text-meta text-ink-faint">Engin heimild inniheldur leitina.</p>
            )}
            <div className="-ml-1">
              {visibleGroups.map((node) => (
                <FacetNode
                  key={node.key}
                  node={node}
                  selected={selected}
                  depth={0}
                  onToggle={(key) => onChange({ scope: toggleScope(catalog, state.scope, key) })}
                />
              ))}
            </div>
          </section>
        </div>

        {open && (
          <div className="flex items-center gap-2 border-t border-border p-3 lg:hidden">
            {filterCount > 0 && (
              <button
                type="button"
                onClick={() => onChange(CLEAR_FILTERS)}
                className="h-11 shrink-0 rounded-md border border-border px-4 text-meta text-ink hover:border-border-strong"
              >
                Hreinsa allt
              </button>
            )}
            <ShowResultsButton state={state} onClose={onClose} />
          </div>
        )}
      </aside>
    </>
  );
}
