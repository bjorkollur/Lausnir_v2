import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { NavRail } from "./NavRail";

describe("NavRail", () => {
  it("shows all four nav links in order", () => {
    renderWithProviders(<NavRail />);
    expect(screen.getByRole("link", { name: /Leit/i })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: /Lagasafn/i })).toHaveAttribute("href", "/lagasafn");
    expect(screen.getByRole("link", { name: /Heimildir/i })).toHaveAttribute("href", "/heimildir");
    expect(screen.getByRole("link", { name: /Bókasafn/i })).toHaveAttribute("href", "/bokasafn");
  });
});
