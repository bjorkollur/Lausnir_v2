import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FacetNode } from "./FacetNode";

const node = { key: "domstolar", label: "Dómstólar", count: 4874, children: [
  { key: "haestirettur", label: "Hæstiréttur", count: 2157, children: [
    { key: "haestirettur_domar", label: "Dómar", count: 712 }] }] };

describe("FacetNode", () => {
  it("shows label + count and toggles on checkbox", async () => {
    const onToggle = vi.fn();
    render(<FacetNode node={node} selected={new Set()} depth={0} onToggle={onToggle} />);
    expect(screen.getByText("Dómstólar")).toBeInTheDocument();
    expect(screen.getByText("4.874")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /Dómstólar/ }));
    expect(onToggle).toHaveBeenCalledWith("domstolar");
  });
});
