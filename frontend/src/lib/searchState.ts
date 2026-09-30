import type { Mode, Sort } from "../api/types";

export interface SearchState {
  q: string;
  mode: Mode;
  scope: string[];
  date_from?: string;
  date_to?: string;
  sort: Sort;
  regex_fields: string[];
  proximity_n: number;
  provision?: string;
  keyword?: string;
}

export const DEFAULT_STATE: SearchState = {
  q: "",
  mode: "keyword",
  scope: [],
  sort: "relevance",
  regex_fields: [],
  proximity_n: 5,
};

const MODES: Mode[] = [
  "keyword", "exact", "prefix", "substring", "any", "proximity", "regex",
];
const SORTS: Sort[] = ["relevance", "newest", "oldest"];

export function parseSearchState(sp: URLSearchParams): SearchState {
  const mode = sp.get("mode");
  const sort = sp.get("sort");
  const rawN = parseInt(sp.get("proximity_n") ?? "5", 10);
  const proximity_n = Number.isFinite(rawN) && rawN >= 1 && rawN <= 50 ? rawN : 5;
  return {
    q: sp.get("q") ?? "",
    mode: MODES.includes(mode as Mode) ? (mode as Mode) : "keyword",
    sort: SORTS.includes(sort as Sort) ? (sort as Sort) : "relevance",
    scope: sp.getAll("scope"),
    regex_fields: sp.getAll("regex_fields"),
    date_from: sp.get("date_from") ?? undefined,
    date_to: sp.get("date_to") ?? undefined,
    proximity_n,
    provision: sp.get("provision") ?? undefined,
    keyword: sp.get("keyword") ?? undefined,
  };
}

export function toSearchParams(s: SearchState): URLSearchParams {
  const sp = new URLSearchParams();
  if (s.q) sp.set("q", s.q);
  sp.set("mode", s.mode);
  sp.set("sort", s.sort);
  if (s.date_from) sp.set("date_from", s.date_from);
  if (s.date_to) sp.set("date_to", s.date_to);
  // Only persist proximity_n when non-default to keep URLs clean
  if (s.mode === "proximity" && s.proximity_n !== 5) {
    sp.set("proximity_n", String(s.proximity_n));
  }
  if (s.provision) sp.set("provision", s.provision);
  if (s.keyword) sp.set("keyword", s.keyword);
  for (const x of s.scope) sp.append("scope", x);
  for (const f of s.regex_fields) sp.append("regex_fields", f);
  return sp;
}

/** Anything that narrows the corpus. A search with none of these and no query
 *  is the landing page; a search with only these is a browse ("everything from
 *  Hæstiréttur", "everything citing 72. gr."). */
export function activeFilterCount(s: SearchState): number {
  return (
    s.scope.length +
    (s.date_from || s.date_to ? 1 : 0) +
    (s.provision ? 1 : 0) +
    (s.keyword ? 1 : 0)
  );
}

export function hasSearchCriteria(s: SearchState): boolean {
  return Boolean(s.q) || activeFilterCount(s) > 0;
}

/** The patch that removes every filter and keeps the query, mode and sort. */
export const CLEAR_FILTERS: Partial<SearchState> = {
  scope: [], date_from: undefined, date_to: undefined, provision: undefined, keyword: undefined,
};
