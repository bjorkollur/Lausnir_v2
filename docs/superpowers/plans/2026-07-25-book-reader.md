# Lestur á heilli bók Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 15+ second blank-page stall when opening a full lögfræðibók (up to 1.4M characters of markdown) on `/domur/{id}`, by lazily splitting and rendering the markdown so only content near the viewport is parsed, while leaving the existing reading experience for all ~89,000 short documents (dómar, ritgerðir) completely unchanged.

**Architecture:** A pure function `splitMarkdown()` cuts a large markdown string into ~500-word, non-overlapping segments at paragraph boundaries. A new `LazyMarkdownSection` component defers `<ReactMarkdown>` parsing of its segment until an `IntersectionObserver` reports it's near the viewport, using `content-visibility: auto` for browser-level layout/paint savings once mounted. `DocPanel.tsx` picks this path only when the document's rendered content exceeds a size threshold — every other document keeps rendering through the exact same single `<ReactMarkdown>` call it uses today.

**Tech Stack:** React 19, TypeScript, `react-markdown` (already a dependency, no version change), Vitest + `@testing-library/react` (already configured, including a project-wide no-op `IntersectionObserver` stub in `src/test/setup.ts`), native `IntersectionObserver` + CSS `content-visibility` (no new npm dependency).

## Global Constraints

- Test runner: `npx vitest run <path>` (project script `npm test` = `vitest run`, `asyncio`-style auto config not relevant here — this is frontend/TS)
- Test helper: use `renderWithProviders` from `src/test/renderWithProviders.tsx` for any component needing Router/QueryClient context (matches existing `DocPanel.test.tsx`)
- Testing style: query by role/text (`getByRole`, `getByText`), not `data-testid` — matches every existing test file in `src/components/*.test.tsx`. The one exception in this plan (Task 2) queries by `aria-hidden="true"`, which is a real accessibility attribute on the placeholder (not a test-only hook), consistent with this rule.
- `frontend/src/test/setup.ts` already stubs `globalThis.IntersectionObserver` as a no-op (`observe()`/`unobserve()`/`disconnect()` do nothing, callback never fires) — this stub must not be modified; a test that needs the callback to fire installs its own local override and restores the global after
- Threshold constant name: `LARGE_DOC_THRESHOLD`, value `50_000` (characters) — every current document type is far below this; only `logfraedibaekur` documents exceed it
- No backend/API changes — `/api/document/{id}` response shape is untouched; splitting happens entirely client-side on data already being fetched today
- `DocumentDetail` type (already defined in `frontend/src/api/types.ts`): `markdown: string | null`, `body_text: string | null` — `DocPanel.tsx` already computes effective content as `doc.markdown ?? doc.body_text ?? ""`

---

## File Structure

- **Create** `frontend/src/lib/splitMarkdown.ts` — pure function, no React/DOM dependency. Exports `splitMarkdown()` and the `LARGE_DOC_THRESHOLD` constant.
- **Create** `frontend/src/lib/splitMarkdown.test.ts` — unit tests for the pure function.
- **Create** `frontend/src/components/LazyMarkdownSection.tsx` — one lazily-rendered segment.
- **Create** `frontend/src/components/LazyMarkdownSection.test.tsx` — tests both the placeholder state and (via a locally-scoped `IntersectionObserver` override) the visible/rendered state.
- **Modify** `frontend/src/components/DocPanel.tsx` — thread the threshold check and segment list through the existing single `<section className="prose ...">` block; no other part of the file changes.
- **Modify** `frontend/src/components/DocPanel.test.tsx` — the existing test (small doc) stays byte-for-byte unchanged as a regression guard; add one new test for the large-doc branch.

---

### Task 1: `splitMarkdown()` pure function

**Files:**
- Create: `frontend/src/lib/splitMarkdown.ts`
- Test: `frontend/src/lib/splitMarkdown.test.ts`

**Interfaces:**
- Produces: `splitMarkdown(text: string, targetWords?: number): string[]` (default `targetWords = 500`), and `export const LARGE_DOC_THRESHOLD = 50_000`. Task 2's tests build fixtures with this function directly; Task 3 (`DocPanel.tsx`) imports both `splitMarkdown` and `LARGE_DOC_THRESHOLD`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/lib/splitMarkdown.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { splitMarkdown } from "./splitMarkdown";

function words(n: number, wordsPerParagraph = 50): string {
  const paragraphs: string[] = [];
  for (let i = 0; i < n; i += wordsPerParagraph) {
    const count = Math.min(wordsPerParagraph, n - i);
    const para = Array.from({ length: count }, (_, j) => `word${i + j}`).join(" ");
    paragraphs.push(para);
  }
  return paragraphs.join("\n\n");
}

describe("splitMarkdown", () => {
  it("returns empty array for empty text", () => {
    expect(splitMarkdown("")).toEqual([]);
  });

  it("returns empty array for whitespace-only text", () => {
    expect(splitMarkdown("   \n\n  ")).toEqual([]);
  });

  it("returns the whole text as one segment when under the target word count", () => {
    const text = words(80);
    expect(splitMarkdown(text)).toEqual([text]);
  });

  it("splits long text into multiple segments", () => {
    const text = words(2000);
    const segments = splitMarkdown(text);
    expect(segments.length).toBeGreaterThan(1);
  });

  it("keeps every word from the source across the produced segments", () => {
    const text = words(1500);
    const segments = splitMarkdown(text);
    const sourceWords = new Set(text.split(/\s+/));
    const segmentWords = new Set(segments.join(" ").split(/\s+/));
    for (const w of sourceWords) {
      expect(segmentWords.has(w)).toBe(true);
    }
  });

  it("produces no empty segments", () => {
    const text = words(1500);
    for (const s of splitMarkdown(text)) {
      expect(s.trim()).not.toBe("");
    }
  });

  it("respects a custom targetWords value", () => {
    const text = words(300, 30);
    const segments = splitMarkdown(text, 100);
    expect(segments.length).toBeGreaterThan(1);
  });

  it("keeps a single huge paragraph (no blank-line breaks) as one segment", () => {
    const text = Array.from({ length: 1000 }, (_, i) => `word${i}`).join(" ");
    expect(splitMarkdown(text)).toEqual([text]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run frontend/src/lib/splitMarkdown.test.ts`
Expected: FAIL with `Cannot find module './splitMarkdown'` (or similar resolution error) — the module doesn't exist yet.

- [ ] **Step 3: Write the implementation**

Create `frontend/src/lib/splitMarkdown.ts`:

```ts
export const LARGE_DOC_THRESHOLD = 50_000;

/** Split markdown into non-overlapping, paragraph-safe segments of ~targetWords each.
 *
 * Unlike the backend's document_chunks (which overlap by design for FTS relevance
 * context), a reader must never show the same text twice, so this carries no overlap.
 */
export function splitMarkdown(text: string, targetWords = 500): string[] {
  if (!text || !text.trim()) return [];

  const words = text.trim().split(/\s+/);
  if (words.length < targetWords) return [text];

  const paragraphs = text.split("\n\n").filter((p) => p.trim() !== "");
  const segments: string[] = [];
  let current: string[] = [];
  let currentWords = 0;

  for (const para of paragraphs) {
    const paraWords = para.trim().split(/\s+/).length;
    current.push(para);
    currentWords += paraWords;
    if (currentWords >= targetWords) {
      segments.push(current.join("\n\n"));
      current = [];
      currentWords = 0;
    }
  }
  if (current.length > 0) {
    segments.push(current.join("\n\n"));
  }
  return segments;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run frontend/src/lib/splitMarkdown.test.ts`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/splitMarkdown.ts frontend/src/lib/splitMarkdown.test.ts
git commit -m "feat: add splitMarkdown pure function for lazy book rendering"
```

---

### Task 2: `LazyMarkdownSection` component

**Files:**
- Create: `frontend/src/components/LazyMarkdownSection.tsx`
- Test: `frontend/src/components/LazyMarkdownSection.test.tsx`

**Interfaces:**
- Consumes: nothing from Task 1 directly (it renders whatever segment string it's given — Task 3 is the one that calls `splitMarkdown()` and passes each result string as `text`)
- Produces: `LazyMarkdownSection({ text: string }): JSX.Element`, a named export. Task 3 imports and renders one per segment, keyed by index.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/LazyMarkdownSection.test.tsx`:

```tsx
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { LazyMarkdownSection } from "./LazyMarkdownSection";

describe("LazyMarkdownSection", () => {
  it("renders an aria-hidden placeholder before becoming visible", () => {
    // Uses the project-wide no-op IntersectionObserver stub from src/test/setup.ts —
    // observe() never fires its callback, so the section never becomes visible.
    const { container } = render(<LazyMarkdownSection text="## Titill\n\nMeginmál hér." />);
    expect(screen.queryByRole("heading", { name: "Titill" })).not.toBeInTheDocument();
    const placeholder = container.querySelector('[aria-hidden="true"]');
    expect(placeholder).not.toBeNull();
  });

  describe("once intersecting", () => {
    let capturedCallback: IntersectionObserverCallback | null = null;
    const realIO = globalThis.IntersectionObserver;

    beforeEach(() => {
      capturedCallback = null;
      globalThis.IntersectionObserver = class {
        constructor(cb: IntersectionObserverCallback) {
          capturedCallback = cb;
        }
        observe() {}
        unobserve() {}
        disconnect() {}
      } as unknown as typeof IntersectionObserver;
    });

    afterEach(() => {
      globalThis.IntersectionObserver = realIO;
    });

    it("renders the markdown content once the observer reports intersection", () => {
      render(<LazyMarkdownSection text="## Titill\n\nMeginmál hér." />);
      expect(screen.queryByRole("heading", { name: "Titill" })).not.toBeInTheDocument();

      capturedCallback!(
        [{ isIntersecting: true } as IntersectionObserverEntry],
        {} as IntersectionObserver,
      );

      expect(screen.getByRole("heading", { name: "Titill" })).toBeInTheDocument();
      expect(screen.getByText("Meginmál hér.")).toBeInTheDocument();
    });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run frontend/src/components/LazyMarkdownSection.test.tsx`
Expected: FAIL with a module resolution error — the component doesn't exist yet.

- [ ] **Step 3: Write the implementation**

Create `frontend/src/components/LazyMarkdownSection.tsx`:

```tsx
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

/** Rough px-per-word estimate for the placeholder height, so the scrollbar
 * doesn't jump when a section swaps from placeholder to real content. */
function estimatedHeightPx(text: string): number {
  const words = text.trim().split(/\s+/).length;
  return Math.max(200, Math.round(words * 7));
}

export function LazyMarkdownSection({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) setVisible(true);
      },
      { rootMargin: "600px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const heightPx = estimatedHeightPx(text);

  if (!visible) {
    return <div ref={ref} aria-hidden="true" style={{ height: heightPx }} />;
  }

  return (
    <section
      ref={ref}
      style={{ contentVisibility: "auto", containIntrinsicSize: `${heightPx}px` }}
    >
      <ReactMarkdown>{text}</ReactMarkdown>
    </section>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run frontend/src/components/LazyMarkdownSection.test.tsx`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/LazyMarkdownSection.tsx frontend/src/components/LazyMarkdownSection.test.tsx
git commit -m "feat: add LazyMarkdownSection for deferred markdown rendering"
```

---

### Task 3: Wire into `DocPanel.tsx`

**Files:**
- Modify: `frontend/src/components/DocPanel.tsx`
- Modify: `frontend/src/components/DocPanel.test.tsx`

**Interfaces:**
- Consumes: `splitMarkdown(text, targetWords?)` and `LARGE_DOC_THRESHOLD` (Task 1), `LazyMarkdownSection({ text })` (Task 2)

- [ ] **Step 1: Write the failing test for the large-document branch**

Read the current `frontend/src/components/DocPanel.test.tsx` — it has one existing test (`renders keywords, reifun, body heading and appeal link`) using a short `body_text`/`markdown` fixture, with a module-level `const doc = {...}` fixture object above the `describe` block. Leave that existing test and the `doc` constant completely unchanged — it is the regression guard proving small documents keep rendering exactly as before, and the new test below reuses the same `doc` constant via spread. Append the new `describe` block below the existing one, in the same file:

```tsx
import { LARGE_DOC_THRESHOLD } from "../lib/splitMarkdown";

function words(n: number, wordsPerParagraph = 50): string {
  const paragraphs: string[] = [];
  for (let i = 0; i < n; i += wordsPerParagraph) {
    const count = Math.min(wordsPerParagraph, n - i);
    const para = Array.from({ length: count }, (_, j) => `word${i + j}`).join(" ");
    paragraphs.push(para);
  }
  return paragraphs.join("\n\n");
}

describe("DocPanel with a large (book-length) document", () => {
  it("renders lazy placeholder sections instead of one direct ReactMarkdown call", () => {
    const bigMarkdown = "## Upphafskafli\n\n" + words(Math.ceil(LARGE_DOC_THRESHOLD / 5));
    const bigDoc = { ...doc, markdown: bigMarkdown, body_text: bigMarkdown };
    const { container } = renderWithProviders(<DocPanel doc={bigDoc} />);

    // The project-wide no-op IntersectionObserver stub (src/test/setup.ts) never
    // fires, so every section stays an aria-hidden placeholder — proving the large
    // document takes the lazy path instead of rendering everything immediately.
    expect(screen.queryByRole("heading", { name: "Upphafskafli" })).not.toBeInTheDocument();
    const placeholders = container.querySelectorAll('[aria-hidden="true"]');
    expect(placeholders.length).toBeGreaterThan(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx vitest run frontend/src/components/DocPanel.test.tsx`
Expected: FAIL — either a module resolution error for `LARGE_DOC_THRESHOLD` import, or the new test finds zero `[aria-hidden="true"]` elements (current `DocPanel.tsx` has no such branch yet).

- [ ] **Step 3: Modify `DocPanel.tsx`**

In `frontend/src/components/DocPanel.tsx`, add the imports:

```tsx
import { splitMarkdown, LARGE_DOC_THRESHOLD } from "../lib/splitMarkdown";
import { LazyMarkdownSection } from "./LazyMarkdownSection";
```

Then replace this block:

```tsx
        <section className="prose prose-slate max-w-none prose-headings:font-bold prose-headings:text-base">
          <ReactMarkdown>{doc.markdown ?? doc.body_text ?? ""}</ReactMarkdown>
        </section>
```

with:

```tsx
        <section className="prose prose-slate max-w-none prose-headings:font-bold prose-headings:text-base">
          {(() => {
            const content = doc.markdown ?? doc.body_text ?? "";
            if (content.length <= LARGE_DOC_THRESHOLD) {
              return <ReactMarkdown>{content}</ReactMarkdown>;
            }
            return splitMarkdown(content).map((segment, i) => (
              <LazyMarkdownSection key={i} text={segment} />
            ));
          })()}
        </section>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run frontend/src/components/DocPanel.test.tsx`
Expected: 2 passed (the original small-doc test unchanged and passing, plus the new large-doc test)

- [ ] **Step 5: Run the full frontend suite to confirm no regressions**

Run: `npm test` (from the `frontend/` directory)
Expected: all tests pass, same total count as before plus the new ones added in this plan (8 in Task 1 + 2 in Task 2 + 1 in Task 3 = 11 new tests; 0 removed or changed elsewhere)

- [ ] **Step 6: Manual verification against the real book in the running dev server**

With the backend (`uv run uvicorn engine.api.app:app --reload --port 8077`) and frontend (`npm run dev` in `frontend/`) both running, open `/domur/{id}` for the `Afbrot og refsiábyrgð. 1` document (find its id with `psql lausnir_v2 -t -c "SELECT d.id FROM documents d JOIN sources s ON s.id=d.source_id WHERE s.short_name='logfraedibaekur' AND case_number LIKE 'Afbrot%';"`).

Expected: the page shows the document header and the first section's content within a couple of seconds (not 15+), and scrolling down progressively reveals further sections without a long freeze. Confirm in the browser devtools Elements panel that sections far below the current scroll position are still `aria-hidden` placeholders (not yet swapped to rendered `<section>` markdown), proving the lazy behavior is active for this document.

Also open a normal, short document (e.g. any `haestirettur` result) and confirm it still renders exactly as before — instant, no placeholders, single `<ReactMarkdown>` output — since its content is far under `LARGE_DOC_THRESHOLD`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/DocPanel.tsx frontend/src/components/DocPanel.test.tsx
git commit -m "feat: lazily render book-length documents in DocPanel"
```

---

## Out of scope (per the design spec, not part of this plan)

- Table of contents / chapter navigation within a book.
- Full-text search inside an open book beyond the browser's native Ctrl+F (which only searches currently-rendered/visible sections — a known limitation, not addressed here).
- Downloading/printing the book as PDF from the reader view — the original PDF is already on disk in the RAW layer if needed directly.
- Backend/API changes — the 2.8MB single-response payload for a book is unchanged; only client-side rendering is addressed.
