import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { NavRail } from "./NavRail";

describe("NavRail", () => {
  it("shows the two v1 links", () => {
    renderWithProviders(<NavRail />);
    expect(screen.getByRole("link", { name: /Leit/i })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: /Heimildir/i })).toHaveAttribute("href", "/heimildir");
  });
});
