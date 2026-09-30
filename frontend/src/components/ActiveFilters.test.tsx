import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ActiveFilters } from "./ActiveFilters";
import { DEFAULT_STATE, CLEAR_FILTERS } from "../lib/searchState";

const labelOf = (k: string) => k.toUpperCase();

describe("ActiveFilters", () => {
  it("renders nothing when no filter is in force", () => {
    const { container } = render(
      <ActiveFilters state={{ ...DEFAULT_STATE, q: "x" }} labelOf={labelOf} onChange={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a chip per scope and removes on click", async () => {
    const onChange = vi.fn();
    render(<ActiveFilters state={{ ...DEFAULT_STATE, scope: ["domstolar", "nefndir"] }}
      labelOf={labelOf} onChange={onChange} />);
    expect(screen.getByText("DOMSTOLAR")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /fjarlægja DOMSTOLAR/i }));
    expect(onChange).toHaveBeenCalledWith({ scope: ["nefndir"] });
  });

  it("shows the period, the provision and the keyword, not only sources", async () => {
    const onChange = vi.fn();
    render(
      <ActiveFilters
        state={{ ...DEFAULT_STATE, date_from: "2020-03-01", provision: "72. gr. laga nr. 33/1944", keyword: "Gæsluvarðhald" }}
        labelOf={labelOf}
        onChange={onChange}
      />,
    );
    expect(screen.getByText("Frá 1. mars 2020")).toBeInTheDocument();
    expect(screen.getByText("72. gr. laga nr. 33/1944")).toBeInTheDocument();
    expect(screen.getByText("Lykilorð: Gæsluvarðhald")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /fjarlægja Frá 1\. mars 2020/i }));
    expect(onChange).toHaveBeenCalledWith({ date_from: undefined, date_to: undefined });
  });

  it("clears every filter at once, keeping the query", async () => {
    const onChange = vi.fn();
    render(
      <ActiveFilters
        state={{ ...DEFAULT_STATE, q: "x", scope: ["domstolar"], keyword: "A" }}
        labelOf={labelOf}
        onChange={onChange}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Hreinsa allt" }));
    expect(onChange).toHaveBeenCalledWith(CLEAR_FILTERS);
    expect(CLEAR_FILTERS).not.toHaveProperty("q");
  });
});
