import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { DocHeader } from "./DocHeader";
import type { DocumentDetail } from "../api/types";

const doc = { id: "a", urlausn: "Hrd. 1/2020", url: null } as unknown as DocumentDetail;

function Where() {
  const l = useLocation();
  return <p data-testid="where">{l.pathname + l.search}</p>;
}

describe("DocHeader", () => {
  it("steps back to the search it was opened from, query and filters intact", async () => {
    render(
      <MemoryRouter initialEntries={["/?q=gæsla&scope=haestirettur", "/domur/a"]} initialIndex={1}>
        <Routes>
          <Route path="/" element={<Where />} />
          <Route path="/domur/:id" element={<DocHeader doc={doc} />} />
        </Routes>
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Til baka" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/?q=gæsla&scope=haestirettur");
  });

  it("falls back to the front page when the document was opened directly", async () => {
    render(
      <MemoryRouter initialEntries={["/domur/a"]}>
        <Routes>
          <Route path="/" element={<Where />} />
          <Route path="/domur/:id" element={<DocHeader doc={doc} />} />
        </Routes>
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Til baka í leit" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/");
  });
});
