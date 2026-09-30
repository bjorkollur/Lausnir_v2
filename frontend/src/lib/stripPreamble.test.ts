import { describe, it, expect } from "vitest";
import { stripDocumentPreamble } from "./stripPreamble";

const GENERATED = `##### https://island.is/domar/s-BE9268C3
# Dómur Hæstaréttar – 26/2026
## 15. september 2026

### Aðilar
Þróunarfélag Hafna ehf. (Karl Georg Sigurbjörnsson lögmaður)

gegn

Húsasmiðjunni ehf. (Jóhannes Karl Sveinsson lögmaður)

### Lykilorð
Kærumál. Dómsmálagjöld. Aðfinnslur.

### Reifun
Kærður var úrskurður Landsréttar.

## Dómur Hæstaréttar
1. Mál þetta dæma hæstaréttardómararnir A og B.
`;

describe("stripDocumentPreamble", () => {
  it("drops the generated header and keeps the body", () => {
    const out = stripDocumentPreamble(GENERATED);
    expect(out.startsWith("## Dómur Hæstaréttar")).toBe(true);
    for (const gone of ["island.is/domar", "### Aðilar", "### Lykilorð", "### Reifun",
                        "Þróunarfélag Hafna", "Kærumál. Dómsmálagjöld"]) {
      expect(out).not.toContain(gone);
    }
    expect(out).toContain("hæstaréttardómararnir A og B");
  });

  it("copes with a document that has no url, no date and no reifun", () => {
    const out = stripDocumentPreamble(
      "# Úrskurður Landsréttar – 5/2020\n\n### Lykilorð\nKærumál.\n\n## Úrskurður Landsréttar\nTexti.\n",
    );
    expect(out).toBe("## Úrskurður Landsréttar\nTexti.\n");
  });

  it("keeps a body heading that merely looks like metadata prose", () => {
    const out = stripDocumentPreamble(
      "# Dómur Hæstaréttar – 1/2020\n## 1. janúar 2020\n\n### Niðurstaða\nTexti.\n",
    );
    expect(out).toBe("### Niðurstaða\nTexti.\n");
  });

  it("leaves markdown that was not built by the renderer untouched", () => {
    const raw = "Bara venjulegur texti án fyrirsagnar.\n\nAnnar málsgrein.";
    expect(stripDocumentPreamble(raw)).toBe(raw);
  });

  it("returns an empty string for a header-only stub", () => {
    expect(
      stripDocumentPreamble("##### https://x\n# Dómur Hæstaréttar – 2/2020\n## 2. janúar 2020\n"),
    ).toBe("");
  });

  it("passes through empty input", () => {
    expect(stripDocumentPreamble("")).toBe("");
  });
});
