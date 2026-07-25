import { useQuery } from "@tanstack/react-query";
import { fetchLaw } from "../api/client";

export function useLaw(id: string) {
  return useQuery({
    queryKey: ["law", id],
    queryFn: () => fetchLaw(id),
    enabled: !!id,
    staleTime: 5 * 60 * 1000,
  });
}
