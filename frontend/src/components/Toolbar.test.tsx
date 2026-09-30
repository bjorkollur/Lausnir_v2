import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { SortSelect } from "./Toolbar";
import { DEFAULT_STATE } from "../lib/searchState";

describe("SortSelect", () => {
  it("shows relevance for a ranked query", () => {
    render(<SortSelect state={{ ...DEFAULT_STATE, q: "gæsla" }} onChange={vi.fn()} />);
    expect(screen.getByRole("combobox", { name: "Röðun" })).toHaveValue("relevance");
  });

  it("shows newest-first when there is no query to rank by", () => {
    // A browse (sources only) is sorted by date on the server; the control
    // used to claim "Bestar niðurstöður" over it.
    render(<SortSelect state={{ ...DEFAULT_STATE, scope: ["haestirettur"] }} onChange={vi.fn()} />);
    expect(screen.getByRole("combobox", { name: "Röðun" })).toHaveValue("newest");
    expect(screen.getByRole("option", { name: "Bestar niðurstöður" })).toBeDisabled();
  });
});
