import { useEffect, useRef, type RefObject } from "react";
import { Routes, Route, useLocation } from "react-router-dom";
import { TopNav } from "./components/TopNav";
import SearchPage from "./routes/SearchPage";
import CatalogPage from "./routes/CatalogPage";
import DocumentPage from "./routes/DocumentPage";
import LagasafnPage from "./routes/LagasafnPage";
import LagasafnKafliPage from "./routes/LagasafnKafliPage";
import LawPage from "./routes/LawPage";
import BokasafnPage from "./routes/BokasafnPage";
import { useScrollMemory } from "./lib/useScrollMemory";

/** A route change in a single-page app moves nothing: focus stays on a link
 *  that no longer exists, and a screen reader hears nothing. Moving focus to
 *  the content region puts the next Tab on the new page's first control.
 *  Skipped when the page has already placed focus itself (the landing page
 *  focuses its search field). */
function useRouteFocus(ref: RefObject<HTMLElement | null>) {
  const { pathname } = useLocation();
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const el = ref.current;
    if (!el || el.contains(document.activeElement)) return;
    el.focus({ preventScroll: true });
  }, [pathname, ref]);
}

export default function App() {
  const content = useRef<HTMLDivElement>(null);
  useScrollMemory(content, "efni");
  useRouteFocus(content);

  return (
    <div className="flex h-full flex-col">
      <TopNav />
      {/* scroll-pt-14 keeps a focused target clear of the sticky chrome:
          WCAG 2.2 AA (Focus Not Obscured) fails when a sticky header covers the
          element the browser just scrolled focus to. */}
      <div
        id="efni"
        ref={content}
        tabIndex={-1}
        className="min-h-0 flex-1 scroll-pt-14 overflow-auto"
      >
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/lagasafn" element={<LagasafnPage />} />
          <Route path="/lagasafn/:n" element={<LagasafnKafliPage />} />
          <Route path="/log/:id" element={<LawPage />} />
          <Route path="/heimildir" element={<CatalogPage />} />
          <Route path="/bokasafn" element={<BokasafnPage />} />
          <Route path="/domur/:id" element={<DocumentPage />} />
        </Routes>
      </div>
    </div>
  );
}
