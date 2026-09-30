import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { LawPanel } from "./LawPanel";
import type { LawDetail } from "../api/types";

const law: LawDetail = {
  id: "l", case_number: "33/1944", law_name: "Stjórnarskrá lýðveldisins Íslands", verdict_type: "Lög",
  document_date: "1944-06-17", url: "https://www.althingi.is/lagasafn/html/nuna/1944033.html",
  kafli: 1, kafli_label: "1. Stjórnskipunarlög o.fl.",
  provisions: [
    { num: 1, text: "Ísland er lýðveldi með þingbundinni stjórn." },
    { num: 72, text: "", sub: [{ num: 1, text: "Eignarrétturinn er friðhelgur." }, { num: 2, text: "Önnur mgr." }] },
  ],
};

describe("LawPanel", () => {
  it("dates the law in Icelandic, once", () => {
    renderWithProviders(<LawPanel law={law} />);
    expect(screen.getByText("Lög nr. 33/1944 · Tók gildi 17. júní 1944")).toBeInTheDocument();
    expect(screen.queryByText(/1944-06-17/)).toBeNull();
  });

  it("links each article to the rulings that cite it", () => {
    renderWithProviders(<LawPanel law={law} />);
    expect(screen.getByRole("link", { name: "Mál sem vísa í 72. gr." })).toHaveAttribute(
      "href",
      `/?provision=${encodeURIComponent("72. gr. laga nr. 33/1944")}`,
    );
  });

  it("leads back up through the chapter to the collection", () => {
    renderWithProviders(<LawPanel law={law} />);
    const crumbs = screen.getByRole("navigation", { name: "Brauðmolar" });
    expect(crumbs).toHaveTextContent("Lagasafn");
    expect(screen.getByRole("link", { name: "1. Stjórnskipunarlög o.fl." })).toHaveAttribute("href", "/lagasafn/1");
  });

  it("renders every paragraph of an article", () => {
    renderWithProviders(<LawPanel law={law} />);
    expect(screen.getByText("Eignarrétturinn er friðhelgur.")).toBeInTheDocument();
    expect(screen.getByText("Önnur mgr.")).toBeInTheDocument();
  });
});
