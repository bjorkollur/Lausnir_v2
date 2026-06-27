import { describe, it, expect } from "vitest";
import { parseSearchState, toSearchParams, DEFAULT_STATE } from "./searchState";

describe("searchState", () => {
  it("defaults when params empty", () => {
    const s = parseSearchState(new URLSearchParams(""));
    expect(s).toEqual(DEFAULT_STATE);
  });
  it("round-trips multi-value scope and regex_fields", () => {
    const sp = new URLSearchParams("q=x&mode=regex&sort=newest&scope=domstolar&scope=nefndir&regex_fields=body_text&date_from=2020-01-01");
    const s = parseSearchState(sp);
    expect(s.scope).toEqual(["domstolar", "nefndir"]);
    expect(s.mode).toBe("regex");
    expect(s.date_from).toBe("2020-01-01");
    expect(toSearchParams(s).toString()).toBe(
      new URLSearchParams("q=x&mode=regex&sort=newest&date_from=2020-01-01&scope=domstolar&scope=nefndir&regex_fields=body_text").toString()
    );
  });
  it("ignores invalid mode/sort", () => {
    const s = parseSearchState(new URLSearchParams("mode=bogus&sort=bogus"));
    expect(s.mode).toBe("keyword");
    expect(s.sort).toBe("relevance");
  });
});
