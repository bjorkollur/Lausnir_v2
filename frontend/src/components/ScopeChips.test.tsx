import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ScopeChips } from "./ScopeChips";
import { DEFAULT_STATE } from "../lib/searchState";

describe("ScopeChips", () => {
  it("renders a chip per scope and removes on click", async () => {
    const onChange = vi.fn();
    render(<ScopeChips state={{ ...DEFAULT_STATE, scope: ["domstolar", "nefndir"] }}
      labelOf={(k) => k.toUpperCase()} onChange={onChange} />);
    expect(screen.getByText("DOMSTOLAR")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /fjarlægja DOMSTOLAR/i }));
    expect(onChange).toHaveBeenCalledWith({ scope: ["nefndir"] });
  });
});
