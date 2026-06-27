import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SearchBar } from "./SearchBar";
import { DEFAULT_STATE } from "../lib/searchState";

describe("SearchBar", () => {
  it("submits typed query on Enter", async () => {
    const onChange = vi.fn();
    render(<SearchBar state={DEFAULT_STATE} onChange={onChange} />);
    await userEvent.type(screen.getByRole("searchbox"), "gæsla{Enter}");
    expect(onChange).toHaveBeenCalledWith({ q: "gæsla" });
  });
  it("shows regex placeholder in regex mode", () => {
    render(<SearchBar state={{ ...DEFAULT_STATE, mode: "regex" }} onChange={vi.fn()} />);
    expect(screen.getByRole("searchbox")).toHaveAttribute("placeholder", expect.stringMatching(/regex/i));
  });
});
