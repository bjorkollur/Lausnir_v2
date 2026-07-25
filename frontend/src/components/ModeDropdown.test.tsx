import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ModeDropdown } from "./ModeDropdown";
import type { SearchState } from "../lib/searchState";
import { DEFAULT_STATE } from "../lib/searchState";

const make = (overrides?: Partial<SearchState>): SearchState => ({
  ...DEFAULT_STATE,
  ...overrides,
});

describe("ModeDropdown", () => {
  it("renders all 7 mode options", () => {
    render(<ModeDropdown state={make()} onChange={() => {}} />);
    const select = screen.getByRole("combobox", { name: /leitarstilling/i });
    const options = Array.from((select as HTMLSelectElement).options).map((o) => o.value);
    expect(options).toEqual([
      "keyword", "exact", "prefix", "substring", "any", "proximity", "regex",
    ]);
  });

  it("shows proximity N input only when mode=proximity", () => {
    const { rerender } = render(<ModeDropdown state={make()} onChange={() => {}} />);
    expect(screen.queryByRole("spinbutton")).toBeNull();

    rerender(<ModeDropdown state={make({ mode: "proximity" })} onChange={() => {}} />);
    expect(screen.getByRole("spinbutton")).toBeTruthy();
  });

  it("proximity N input shows current value", () => {
    render(
      <ModeDropdown state={make({ mode: "proximity", proximity_n: 10 })} onChange={() => {}} />
    );
    const input = screen.getByRole("spinbutton") as HTMLInputElement;
    expect(input.value).toBe("10");
  });

  it("changing mode calls onChange with new mode", () => {
    const onChange = vi.fn();
    render(<ModeDropdown state={make()} onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox", { name: /leitarstilling/i }), {
      target: { value: "exact" },
    });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ mode: "exact" }));
  });

  it("switching from keyword to exact auto-switches sort to newest", () => {
    const onChange = vi.fn();
    render(<ModeDropdown state={make({ mode: "keyword", sort: "relevance" })} onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox", { name: /leitarstilling/i }), {
      target: { value: "exact" },
    });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ mode: "exact", sort: "newest" })
    );
  });

  it("switching to proximity keeps relevance sort", () => {
    const onChange = vi.fn();
    render(<ModeDropdown state={make({ mode: "keyword", sort: "relevance" })} onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox", { name: /leitarstilling/i }), {
      target: { value: "proximity" },
    });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ mode: "proximity", sort: "relevance" })
    );
  });

  it("changing proximity_n calls onChange with new value", () => {
    const onChange = vi.fn();
    render(
      <ModeDropdown state={make({ mode: "proximity", proximity_n: 5 })} onChange={onChange} />
    );
    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "12" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ proximity_n: 12 }));
  });
});
