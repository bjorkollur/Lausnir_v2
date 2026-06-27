import type { Mode, Sort } from "../api/types";

export interface SearchState {
  q: string;
  mode: Mode;
  scope: string[];
  date_from?: string;
  date_to?: string;
  sort: Sort;
  regex_fields: string[];
}

export const DEFAULT_STATE: SearchState = {
  q: "",
  mode: "keyword",
  scope: [],
  sort: "relevance",
  regex_fields: [],
};

const MODES: Mode[] = ["keyword", "regex"];
const SORTS: Sort[] = ["relevance", "newest", "oldest"];

export function parseSearchState(sp: URLSearchParams): SearchState {
  const mode = sp.get("mode");
  const sort = sp.get("sort");
  return {
    q: sp.get("q") ?? "",
    mode: MODES.includes(mode as Mode) ? (mode as Mode) : "keyword",
    sort: SORTS.includes(sort as Sort) ? (sort as Sort) : "relevance",
    scope: sp.getAll("scope"),
    regex_fields: sp.getAll("regex_fields"),
    date_from: sp.get("date_from") ?? undefined,
    date_to: sp.get("date_to") ?? undefined,
  };
}

export function toSearchParams(s: SearchState): URLSearchParams {
  const sp = new URLSearchParams();
  if (s.q) sp.set("q", s.q);
  sp.set("mode", s.mode);
  sp.set("sort", s.sort);
  if (s.date_from) sp.set("date_from", s.date_from);
  if (s.date_to) sp.set("date_to", s.date_to);
  for (const x of s.scope) sp.append("scope", x);
  for (const f of s.regex_fields) sp.append("regex_fields", f);
  return sp;
}
