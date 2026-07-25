import "@testing-library/jest-dom/vitest";

// IntersectionObserver is not implemented in jsdom; provide a no-op stub.
if (!globalThis.IntersectionObserver) {
  globalThis.IntersectionObserver = class IntersectionObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof IntersectionObserver;
}
