// GET /api/search?q&mode&scope(repeatable)&date_from&date_to&sort&page&page_size&regex_fields(repeatable)
export interface SearchResponse {
  /** total: reachable results; when relaxed, strict_total + relaxed matches up to the cap. */
  total: number; page: number; page_size: number; results: SearchResult[];
  /** Count of documents matching every keyword (tier 0). */
  strict_total: number;
  /** True when the result set includes relaxed (tier 1/2) matches below strict_total. */
  relaxed: boolean;
}
export interface Party { name: string; lawyer: string | null; }
export interface SearchResult {
  id: string; urlausn: string; source: string; source_display: string;
  court: string | null; case_number: string | null; document_date: string | null;
  verdict_type: string | null; keywords: string[]; plaintiffs: Party[]; defendants: Party[];
  snippet: string; has_appeal_links: boolean;
  /** How many documents in the corpus cite this one (0 when none). */
  cited_by_count: number;
  /** Passage-level fields (null when the API runs the legacy document path). */
  passage_id: string | null; anchor: string | null; section_kind: string | null;
  layer: "summary" | "body" | "lower_body" | null; match_count: number | null;
  /** Relaxed keyword search tier: 0 = all keywords, 1 = most, 2 = some. */
  match_tier: number;
}
// GET /api/facets?q&mode&date_from&date_to&regex_fields  → {catalog, total}
export interface CatalogNode { key: string; label: string; count: number; children?: CatalogNode[]; }
export interface FacetsResponse { catalog: CatalogNode[]; total: number; }
// GET /api/sources → {catalog, sources, regex_fields, total}
export interface SourceFlat { short_name: string; display_name: string; abbreviation: string | null; count: number; }
export interface SourcesResponse { catalog: CatalogNode[]; sources: SourceFlat[]; regex_fields: string[]; total: number; }
// GET /api/document/:id?markdown=true
/** Known link relations. `(string & {})` keeps the union open: the API may add
 * relations before the frontend knows their Icelandic label, and an unknown one
 * is rendered verbatim rather than dropped. */
export type LinkRelation =
  | "appealed_to" | "appealed_from" | "leyfisbeidni_um" | "leiddi_til_doms" | "cites"
  | (string & {});
export interface AppealLink { relation: LinkRelation; confidence: number | null; method: string | null; document_id: string; source: string; urlausn: string; }
/** One resolved case-to-case citation, in either direction. */
export interface CitationRef {
  document_id: string; urlausn: string; source: string; document_date: string | null;
  layer: "summary" | "body"; passage_id: string | null; anchor: string | null;
  raw_text: string; confidence: number | null;
  /** The same pair also carries an appeal relation — shown here, not in "Tengd mál". */
  also_appeal: boolean;
  /** Same court and case number: the other instalment of this very case. */
  same_case: boolean;
}
// GET /api/document/:id/citations?direction=out|in&page&page_size
export interface CitationsResponse {
  direction: "out" | "in"; total: number; page: number; page_size: number; items: CitationRef[];
}
export interface DocumentDetail {
  id: string; source: string; source_display: string; external_id: string; url: string | null;
  urlausn: string; court: string | null; case_number: string | null; document_date: string | null;
  verdict_type: string | null; instance_tier: number | null; case_type: string | null;
  plaintiffs: Party[]; defendants: Party[]; keywords: string[]; summary: string | null;
  body_text: string | null; lower_body_text: string | null; appeal_links: AppealLink[];
  markdown: string | null;
  /** First page of the cases this document cites, and the full count. */
  citations_out: CitationRef[]; citations_out_total: number;
  /** First page of the cases citing this document, and the full count. */
  cited_by: CitationRef[]; cited_by_total: number;
  /** Citations found in the text that no document in the corpus could be matched to. */
  citations_unresolved_total: number;
  /** True for theses/books, where case_number holds a title, not a case number. */
  case_number_is_title: boolean;
  /** Access restriction at the source (Skemman embargo). null = not reported. */
  locked: boolean | null;
  /** Icelandic-format date (dd.mm.yyyy) the embargo lifts, when known. */
  embargo_until: string | null;
  /** True → GET /api/document/:id/pdf serves the original PDF. */
  has_pdf: boolean;
}

export type Mode = "keyword" | "exact" | "prefix" | "substring" | "any" | "proximity" | "regex";
export type Sort = "relevance" | "newest" | "oldest";
export interface SearchParams {
  q: string; mode: Mode; scope: string[];
  date_from?: string; date_to?: string; sort: Sort;
  page?: number; page_size?: number; regex_fields?: string[];
  proximity_n?: number; provision?: string; keyword?: string;
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
