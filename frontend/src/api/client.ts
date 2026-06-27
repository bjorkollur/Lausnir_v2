import type { SearchParams, SearchResponse, FacetsResponse, SourcesResponse, DocumentDetail } from "./types";

const BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8077";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function getJson<T>(path: string, qs?: URLSearchParams): Promise<T> {
  const url = `${BASE}${path}${qs && [...qs].length ? `?${qs}` : ""}`;
  const res = await fetch(url);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

function searchQs(p: SearchParams): URLSearchParams {
  const qs = new URLSearchParams();
  if (p.q) qs.set("q", p.q);
  qs.set("mode", p.mode);
  qs.set("sort", p.sort);
  for (const s of p.scope) qs.append("scope", s);
  if (p.date_from) qs.set("date_from", p.date_from);
  if (p.date_to) qs.set("date_to", p.date_to);
  if (p.page) qs.set("page", String(p.page));
  if (p.page_size) qs.set("page_size", String(p.page_size));
  for (const f of p.regex_fields ?? []) qs.append("regex_fields", f);
  return qs;
}

export const searchDocuments = (p: SearchParams) => getJson<SearchResponse>("/api/search", searchQs(p));

export function fetchFacets(p: Omit<SearchParams, "scope" | "sort" | "page" | "page_size">): Promise<FacetsResponse> {
  const qs = new URLSearchParams();
  if (p.q) qs.set("q", p.q);
  qs.set("mode", p.mode);
  if (p.date_from) qs.set("date_from", p.date_from);
  if (p.date_to) qs.set("date_to", p.date_to);
  for (const f of p.regex_fields ?? []) qs.append("regex_fields", f);
  return getJson<FacetsResponse>("/api/facets", qs);
}

export const fetchSources = () => getJson<SourcesResponse>("/api/sources");
export const fetchDocument = (id: string) => getJson<DocumentDetail>(`/api/document/${id}`);
