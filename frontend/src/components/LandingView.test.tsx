import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LandingView } from "./LandingView";
import { DEFAULT_STATE } from "../lib/searchState";

const catalog = [
  { key: "domstolar", label: "Dómstólar", count: 10, children: [
    { key: "haestirettur", label: "Hæstiréttur", count: 6 },
    { key: "landsrettur", label: "Landsréttur", count: 4 },
  ] },
];

function renderLanding(patch = vi.fn()) {
  render(<LandingView state={DEFAULT_STATE} catalog={catalog} total={10} sourceCount={2} patch={patch} />);
  return patch;
}

describe("LandingView", () => {
  // The folded state is remembered per viewer; start each test from nothing.
  beforeEach(() => {
    try {
      window.localStorage?.clear();
    } catch {
      /* no storage in this environment: the component copes the same way */
    }
  });

  it("keeps advanced search folded until asked for", async () => {
    renderLanding();
    const toggle = screen.getByRole("button", { name: /Ýtarleg leit/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("checkbox", { name: "Hæstiréttur" })).toBeNull();
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("checkbox", { name: "Hæstiréttur" })).toBeInTheDocument();
  });

  it("applies nothing until the form is submitted, then applies it all at once", async () => {
    // Ticking a source used to write it into the URL at once, which turned the
    // page into a results page mid-form and dropped the typed query.
    const patch = renderLanding();
    await userEvent.type(screen.getByRole("searchbox"), "skaðabætur");
    await userEvent.click(screen.getByRole("button", { name: /Ýtarleg leit/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Hæstiréttur" }));
    await userEvent.click(screen.getByRole("radio", { name: "Heilt orð" }));
    expect(patch).not.toHaveBeenCalled();
    expect(screen.getByRole("searchbox")).toHaveValue("skaðabætur");

    await userEvent.click(screen.getAllByRole("button", { name: "Leita" })[0]);
    expect(patch).toHaveBeenCalledTimes(1);
    expect(patch).toHaveBeenCalledWith(
      expect.objectContaining({ q: "skaðabætur", scope: ["haestirettur"], mode: "exact", sort: "newest" }),
    );
  });

  it("searches on a provision alone", async () => {
    const patch = renderLanding();
    await userEvent.click(screen.getByRole("button", { name: /Ýtarleg leit/ }));
    await userEvent.type(screen.getByLabelText("Lagaákvæði"), " 72. gr. laga nr. 33/1944 {Enter}");
    expect(patch).toHaveBeenCalledWith(expect.objectContaining({ q: "", provision: "72. gr. laga nr. 33/1944" }));
  });

  it("does nothing on an empty submit but return to the field", async () => {
    const patch = renderLanding();
    await userEvent.click(screen.getAllByRole("button", { name: "Leita" })[0]);
    expect(patch).not.toHaveBeenCalled();
    expect(screen.getByRole("searchbox")).toHaveFocus();
  });
});
