const MONTHS = [
  "janúar", "febrúar", "mars", "apríl", "maí", "júní",
  "júlí", "ágúst", "september", "október", "nóvember", "desember",
];

/** "2010-06-16" → "16. júní 2010".
 *
 *  Written out rather than left to toLocaleDateString("is-IS"): the ICU data
 *  for Icelandic is not present in every runtime, and the fallback is US
 *  English, which is not an acceptable failure mode for a legal source. */
export function formatIcelandicDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return null;
  const [, year, month, day] = m;
  const name = MONTHS[Number(month) - 1];
  if (!name) return null;
  return `${Number(day)}. ${name} ${year}`;
}
