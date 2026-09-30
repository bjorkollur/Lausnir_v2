/** 44502 → "44.502".
 *
 *  Icelandic groups thousands with a period. toLocaleString("is-IS") gets that
 *  right only where the runtime carries Icelandic ICU data; without it the
 *  fallback is en-US and the app prints "44,502" beside a page that prints
 *  "4570" raw. Three number formats in one product is a correctness problem,
 *  not a style one, so the separator is written out. */
export function formatCount(n: number): string {
  const sign = n < 0 ? "-" : "";
  const digits = Math.abs(Math.trunc(n)).toString();
  let out = "";
  for (let i = 0; i < digits.length; i++) {
    if (i > 0 && (digits.length - i) % 3 === 0) out += ".";
    out += digits[i];
  }
  return sign + out;
}
