import DOMPurify from "dompurify";

export function markHtml(html: string): { __html: string } {
  return { __html: DOMPurify.sanitize(html ?? "", { ALLOWED_TAGS: ["mark"], ALLOWED_ATTR: [] }) };
}
