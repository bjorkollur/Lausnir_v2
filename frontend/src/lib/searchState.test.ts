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

  it("proximity_n round-trips through URL params", () => {
    const sp = new URLSearchParams("q=x&mode=proximity&sort=relevance&proximity_n=10");
    const s = parseSearchState(sp);
    expect(s.proximity_n).toBe(10);
    expect(s.mode).toBe("proximity");
    const out = toSearchParams(s);
    expect(out.get("proximity_n")).toBe("10");
  });

  it("proximity_n defaults to 5 when absent", () => {
    const s = parseSearchState(new URLSearchParams("mode=proximity"));
    expect(s.proximity_n).toBe(5);
  });

  it("proximity_n omitted from URL when default (5)", () => {
    const s = parseSearchState(new URLSearchParams("mode=proximity"));
    const out = toSearchParams(s);
    expect(out.has("proximity_n")).toBe(false);
  });

  it("proximity_n clamps invalid values to default", () => {
    const s = parseSearchState(new URLSearchParams("proximity_n=999"));
    expect(s.proximity_n).toBe(5);
  });

  it("all new modes are valid", () => {
    for (const m of ["exact", "prefix", "substring", "any", "proximity"]) {
      const s = parseSearchState(new URLSearchParams(`mode=${m}`));
      expect(s.mode).toBe(m);
    }
  });
});
