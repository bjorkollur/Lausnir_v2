import { useInfiniteQuery } from "@tanstack/react-query";
import { searchDocuments } from "../api/client";
import type { SearchState } from "../lib/searchState";

const PAGE_SIZE = 20;

export function useSearch(s: SearchState) {
  return useInfiniteQuery({
    queryKey: ["search", s],
    initialPageParam: 1,
    queryFn: ({ pageParam }) =>
      searchDocuments({ ...s, page: pageParam as number, page_size: PAGE_SIZE }),
    getNextPageParam: (last) =>
      last.page * last.page_size < last.total ? last.page + 1 : undefined,
  });
}
