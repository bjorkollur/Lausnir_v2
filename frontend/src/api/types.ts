// GET /api/search?q&mode&scope(repeatable)&date_from&date_to&sort&page&page_size&regex_fields(repeatable)
export interface SearchResponse {
  total: number; page: number; page_size: number; results: SearchResult[];
}
export interface Party { name: string; lawyer: string | null; }
export interface SearchResult {
  id: string; urlausn: string; source: string; source_display: string;
  court: string | null; case_number: string | null; document_date: string | null;
  verdict_type: string | null; keywords: string[]; plaintiffs: Party[]; defendants: Party[];
  snippet: string; has_appeal_links: boolean;
}
// GET /api/facets?q&mode&date_from&date_to&regex_fields  → {catalog, total}
export interface CatalogNode { key: string; label: string; count: number; children?: CatalogNode[]; }
export interface FacetsResponse { catalog: CatalogNode[]; total: number; }
// GET /api/sources → {catalog, sources, regex_fields, total}
export interface SourceFlat { short_name: string; display_name: string; abbreviation: string | null; count: number; }
export interface SourcesResponse { catalog: CatalogNode[]; sources: SourceFlat[]; regex_fields: string[]; total: number; }
// GET /api/document/:id?markdown=true
export interface AppealLink { relation: string; confidence: number | null; method: string | null; document_id: string; source: string; urlausn: string; }
export interface DocumentDetail {
  id: string; source: string; source_display: string; external_id: string; url: string | null;
  urlausn: string; court: string | null; case_number: string | null; document_date: string | null;
  verdict_type: string | null; instance_tier: number | null; case_type: string | null;
  plaintiffs: Party[]; defendants: Party[]; keywords: string[]; summary: string | null;
  body_text: string | null; lower_body_text: string | null; appeal_links: AppealLink[];
  markdown: string | null;
}

export type Mode = "keyword" | "exact" | "prefix" | "substring" | "any" | "proximity" | "regex";
export type Sort = "relevance" | "newest" | "oldest";
export interface SearchParams {
  q: string; mode: Mode; scope: string[];
  date_from?: string; date_to?: string; sort: Sort;
  page?: number; page_size?: number; regex_fields?: string[];
  proximity_n?: number; provision?: string;
}

// GET /api/law/:id
export interface SubProvision {
  num: number;
  text: string;
}

export interface Provision {
  num: number;
  suffix?: string;   // "a", "b" etc. — til staðar aðeins í stafliðagreinum (218. gr. a.)
  text: string;
  sub?: SubProvision[];
}

export interface LawDetail {
  id: string;
  case_number: string | null;
  law_name: string | null;
  verdict_type: string | null;
  document_date: string | null;
  url: string | null;
  kafli: number;
  kafli_label: string;
  provisions: Provision[];
}
