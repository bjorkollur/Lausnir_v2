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
      <div className="min-h-0 flex-1 overflow-auto">
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
