import { useQuery } from "@tanstack/react-query";
import { fetchFacets } from "../api/client";
import type { SearchState } from "../lib/searchState";

export function useFacets(s: SearchState) {
  // NB: scope/sort excluded from the key — facets ignore the source selection.
  return useQuery({
    queryKey: ["facets", s.q, s.mode, s.date_from, s.date_to, s.regex_fields],
    queryFn: () =>
      fetchFacets({
        q: s.q,
        mode: s.mode,
        date_from: s.date_from,
        date_to: s.date_to,
        regex_fields: s.regex_fields,
      }),
  });
}
