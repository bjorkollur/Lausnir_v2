import { useQuery } from "@tanstack/react-query";
import { fetchDocument } from "../api/client";

export function useDocument(id: string) {
  return useQuery({
    queryKey: ["document", id],
    queryFn: () => fetchDocument(id),
    enabled: !!id,
  });
}
