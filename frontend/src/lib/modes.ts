import type { Mode, Sort } from "../api/types";
import type { SearchState } from "./searchState";

/** One definition of the seven search modes. LandingView, ModeDropdown and the
 *  toolbar each used to carry their own copy of these tables. */
export const ALL_MODES: Mode[] = [
  "keyword", "exact", "prefix", "substring", "any", "proximity", "regex",
];

export const MODE_LABELS: Record<Mode, string> = {
  keyword: "Orðaleit",
  exact: "Heilt orð",
  prefix: "Byrjar á",
  substring: "Hluti af orði",
  any: "Eitthvað af",
  proximity: "Nálægt",
  regex: "Regex",
};

/** What each mode actually matches, in the terms engine/search/queries.py uses:
 *  keyword and proximity are lemmatised full-text search, the others are
 *  case-insensitive regex over the text, one AND-fragment per word. */
export const MODE_HINTS: Record<Mode, string> = {
  keyword: "Öll orðin, í hvaða beygingarmynd sem er.",
  exact: "Öll orðin, nákvæmlega eins og þau eru skrifuð.",
  prefix: "Orð sem byrja á leitarorðunum.",
  substring: "Leitarorðin hvar sem er, líka inni í orðum.",
  any: "Að minnsta kosti eitt leitarorðanna.",
  proximity: "Orðin innan tiltekins orðafjölda hvert frá öðru.",
  regex: "Regluleg segð, óháð há- og lágstöfum.",
};

/** Modes ranked by ts_rank. The others have no relevance score, so a
 *  relevance sort silently falls back to date on the server. */
export const FTS_MODES = new Set<Mode>(["keyword", "proximity"]);

/** The sort to request after switching to `mode`. */
export function sortForMode(mode: Mode, sort: Sort): Sort {
  return !FTS_MODES.has(mode) && sort === "relevance" ? "newest" : sort;
}

/** Relevance needs a query to be relevant to and a mode that ranks. Without
 *  either the server sorts by date anyway, so the control says so instead of
 *  showing "Bestar niðurstöður" over a list that is newest-first. */
export function effectiveSort(s: SearchState): Sort {
  return s.sort === "relevance" && (!s.q || !FTS_MODES.has(s.mode)) ? "newest" : s.sort;
}
