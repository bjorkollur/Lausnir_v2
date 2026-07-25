import { useQuery } from "@tanstack/react-query";
import { fetchSources } from "../api/client";

export function useSources() {
  return useQuery({
    queryKey: ["sources"],
    queryFn: fetchSources,
    staleTime: 5 * 60 * 1000,
  });
}
