import { Routes, Route } from "react-router-dom";
import { TopNav } from "./components/TopNav";
import SearchPage from "./routes/SearchPage";
import CatalogPage from "./routes/CatalogPage";
import DocumentPage from "./routes/DocumentPage";
import LagasafnPage from "./routes/LagasafnPage";
import LagasafnKafliPage from "./routes/LagasafnKafliPage";
import LawPage from "./routes/LawPage";
import BokasafnPage from "./routes/BokasafnPage";

export default function App() {
  return (
    <div className="flex h-full flex-col">
      <TopNav />
      {/* scroll-pt-14 keeps a focused target clear of the sticky chrome:
          WCAG 2.2 AA (Focus Not Obscured) fails when a sticky header covers the
          element the browser just scrolled focus to. */}
      <div id="efni" className="min-h-0 flex-1 scroll-pt-14 overflow-auto">
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
