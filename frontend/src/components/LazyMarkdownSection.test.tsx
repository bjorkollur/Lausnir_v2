import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { act } from "react";
import { LazyMarkdownSection } from "./LazyMarkdownSection";

describe("LazyMarkdownSection", () => {
  it("renders an aria-hidden placeholder before becoming visible", () => {
    // Uses the project-wide no-op IntersectionObserver stub from src/test/setup.ts —
    // observe() never fires its callback, so the section never becomes visible.
    const { container } = render(<LazyMarkdownSection text="## Titill\n\nMeginmál hér." />);
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
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
      expect(screen.queryByRole("heading")).not.toBeInTheDocument();

      act(() => {
        capturedCallback!(
          [{ isIntersecting: true } as IntersectionObserverEntry],
          {} as IntersectionObserver,
        );
      });

      expect(screen.getByRole("heading")).toBeInTheDocument();
      expect(screen.getByText(/Titill/)).toBeInTheDocument();
      expect(screen.getByText(/Meginmál hér/)).toBeInTheDocument();
    });
  });
});
