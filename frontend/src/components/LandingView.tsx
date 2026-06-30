import { useState, useRef, type FormEvent } from "react";
import type { CatalogNode } from "../api/types";
import type { Mode, Sort } from "../api/types";
import type { SearchState } from "../lib/searchState";
import { SourceTree } from "./SourceTree";
import { ProvisionInput, KeywordInput } from "./Toolbar";

// ── Mode labels ───────────────────────────────────────────────────────────────

const MODE_LABELS: Record<Mode, string> = {
  keyword: "Orðaleit",
  exact: "Heilt orð",
  prefix: "Byrjar á",
  substring: "Hluti af orði",
  any: "Eitthvað af",
  proximity: "Nálægt",
  regex: "Regex",
};

const ALL_MODES: Mode[] = [
  "keyword",
  "exact",
  "prefix",
  "substring",
  "any",
  "proximity",
  "regex",
];

// Modes that support ts_rank (can use sort=relevance)
const FTS_MODES = new Set<Mode>(["keyword", "proximity"]);

// ── Shared classes ────────────────────────────────────────────────────────────

const SECTION_LABEL =
  "text-[0.7rem] font-semibold text-[var(--ink-soft)] uppercase tracking-[0.12em] mb-3";

// ── LandingView ───────────────────────────────────────────────────────────────

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
  const [localQ, setLocalQ] = useState("");
  const searchInputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (localQ.trim()) {
      patch({ q: localQ.trim() });
    }
  };

  const handleModeChange = (mode: Mode) => {
    const sort: Sort =
      !FTS_MODES.has(mode) && state.sort === "relevance" ? "newest" : state.sort;
    patch({ mode, sort });
  };

  const handleScopeChange = (scope: string[]) => {
    patch({ scope });
  };

  return (
    <div className="min-h-full flex flex-col items-center px-6 pb-20">
      {/* ── Simple search section ───────────────────────────────────────────── */}
      <div className="flex flex-col items-center gap-9 pt-20 pb-14 w-full max-w-2xl">
        {/* Branding — serif wordmark preserved as the brand element */}
        <div className="text-center">
          <h1 className="font-serif text-6xl font-medium tracking-[-0.02em] text-[var(--ink)]">
            Lausnir
          </h1>
          <p className="text-[var(--ink-soft)] mt-3 text-base tracking-wide">
            Íslenskar réttarheimildir
          </p>
        </div>

        {/* Simple search bar */}
        <form onSubmit={handleSubmit} className="w-full flex gap-2.5">
          <input
            ref={searchInputRef}
            autoFocus
            value={localQ}
            onChange={(e) => setLocalQ(e.target.value)}
            placeholder={
              state.mode === "regex" ? "regex mynstur…" : "Leita í réttarheimildum…"
            }
            aria-label="Leitarbox"
            className="flex-1 h-14 rounded-md border border-[var(--border)] bg-[var(--surface)] px-5 text-lg text-[var(--ink)] placeholder:text-[var(--ink-faint)] outline-none focus:border-[var(--accent)] transition-colors"
          />
          <button
            type="submit"
            disabled={!localQ.trim()}
            className="h-14 px-8 bg-[var(--cta)] text-white rounded-md font-medium tracking-wide hover:bg-[var(--cta-hover)] active:scale-[0.98] disabled:opacity-35 disabled:cursor-not-allowed transition-all"
          >
            Leita
          </button>
        </form>

        {/* Structured filters: provision reference + keyword tag */}
        <div className="flex flex-wrap items-center justify-center gap-2">
          <ProvisionInput
            value={state.provision ?? ""}
            onChange={(v) => patch({ provision: v || undefined })}
          />
          <KeywordInput
            value={state.keyword ?? ""}
            onChange={(v) => patch({ keyword: v || undefined })}
          />
        </div>

        {/* Stats footer */}
        <p className="text-xs text-[var(--ink-faint)] tracking-wide">
          {total.toLocaleString("is-IS")} skjöl · {sourceCount} heimildir
        </p>
      </div>

      {/* ── Divider ────────────────────────────────────────────────────────── */}
      <div className="w-full max-w-2xl flex items-center gap-4 mb-10">
        <div className="flex-1 border-t border-[var(--border)]" />
        <span className="text-xs text-[var(--ink-faint)] uppercase tracking-[0.14em]">
          Ýtarleg leit
        </span>
        <div className="flex-1 border-t border-[var(--border)]" />
      </div>

      {/* ── Advanced search section ─────────────────────────────────────────── */}
      <div className="w-full max-w-2xl space-y-9">
        {/* Mode selection */}
        <section>
          <h2 className={SECTION_LABEL}>Leitarstilling</h2>
          <div className="flex flex-wrap gap-2">
            {ALL_MODES.map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => handleModeChange(m)}
                className={`px-4 py-1.5 rounded-md text-sm font-medium border transition-colors ${
                  state.mode === m
                    ? "bg-[var(--accent-soft)] text-[var(--ink)] border-[var(--accent)]"
                    : "bg-[var(--surface)] text-[var(--ink-soft)] border-[var(--border)] hover:border-[var(--border-strong)] hover:text-[var(--ink)]"
                }`}
              >
                {MODE_LABELS[m]}
              </button>
            ))}
          </div>

          {/* Proximity distance picker */}
          {state.mode === "proximity" && (
            <div className="mt-3 flex items-center gap-2 text-sm text-[var(--ink-soft)]">
              <span>Innan</span>
              <input
                type="number"
                min={1}
                max={50}
                value={state.proximity_n}
                onChange={(e) => {
                  const n = parseInt(e.target.value, 10);
                  if (Number.isFinite(n) && n >= 1 && n <= 50) {
                    patch({ proximity_n: n });
                  }
                }}
                className="w-16 border border-[var(--border)] bg-[var(--surface)] rounded-md px-2 py-1 text-center text-[var(--ink)] outline-none focus:border-[var(--accent)] transition-colors"
              />
              <span>orða</span>
            </div>
          )}
        </section>

        {/* Date range */}
        <section>
          <h2 className={SECTION_LABEL}>Tímabil</h2>
          <div className="flex items-center gap-3 flex-wrap">
            <label className="flex items-center gap-2 text-sm text-[var(--ink-soft)]">
              <span className="w-8 text-right text-[var(--ink-faint)]">Frá</span>
              <input
                type="date"
                value={state.date_from ?? ""}
                onChange={(e) =>
                  patch({ date_from: e.target.value || undefined })
                }
                className="border border-[var(--border)] bg-[var(--surface)] rounded-md px-3 py-1.5 text-sm text-[var(--ink)] outline-none focus:border-[var(--accent)] transition-colors"
              />
            </label>
            <label className="flex items-center gap-2 text-sm text-[var(--ink-soft)]">
              <span className="w-8 text-right text-[var(--ink-faint)]">Til</span>
              <input
                type="date"
                value={state.date_to ?? ""}
                onChange={(e) =>
                  patch({ date_to: e.target.value || undefined })
                }
                className="border border-[var(--border)] bg-[var(--surface)] rounded-md px-3 py-1.5 text-sm text-[var(--ink)] outline-none focus:border-[var(--accent)] transition-colors"
              />
            </label>
            {(state.date_from || state.date_to) && (
              <button
                type="button"
                onClick={() => patch({ date_from: undefined, date_to: undefined })}
                className="text-xs text-[var(--ink-faint)] hover:text-[var(--ink)] underline underline-offset-2 transition-colors"
              >
                Hreinsa tímabil
              </button>
            )}
          </div>
        </section>

        {/* Source tree */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className={SECTION_LABEL + " mb-0"}>Heimildir</h2>
            {state.scope.length > 0 && (
              <span className="text-xs text-[var(--accent)] font-medium">
                {state.scope.length} valin
              </span>
            )}
          </div>
          <SourceTree
            catalog={catalog}
            scope={state.scope}
            onScopeChange={handleScopeChange}
          />
        </section>

        {/* Advanced search submit button */}
        <form onSubmit={handleSubmit}>
          <button
            type="submit"
            disabled={!localQ.trim()}
            className="w-full h-12 bg-[var(--cta)] text-white rounded-md font-medium tracking-wide hover:bg-[var(--cta-hover)] active:scale-[0.99] disabled:opacity-35 disabled:cursor-not-allowed transition-all"
          >
            Leita með ýtarlegri leit
          </button>
          {!localQ.trim() && (
            <p className="text-center text-xs text-[var(--ink-faint)] mt-2.5">
              Sláðu inn leitarorð í reitinn að ofan
            </p>
          )}
        </form>
      </div>
    </div>
  );
}
