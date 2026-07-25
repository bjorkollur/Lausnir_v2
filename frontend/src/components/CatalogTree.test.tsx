import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { CatalogTree } from "./CatalogTree";

const nodes = [{ key: "domstolar", label: "Dómstólar", count: 44155, children: [
  { key: "haestirettur", label: "Hæstiréttur", count: 13466 }] }];

describe("CatalogTree", () => {
  it("links each node to a scoped search", () => {
    renderWithProviders(<CatalogTree nodes={nodes} />);
    expect(screen.getByRole("link", { name: /Dómstólar/ })).toHaveAttribute("href", "/?scope=domstolar");
    expect(screen.getByRole("link", { name: /Hæstiréttur/ })).toHaveAttribute("href", "/?scope=haestirettur");
  });
});
