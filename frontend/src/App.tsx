import { Routes, Route } from "react-router-dom";
import { NavRail } from "./components/NavRail";
import SearchPage from "./routes/SearchPage";
import CatalogPage from "./routes/CatalogPage";
import DocumentPage from "./routes/DocumentPage";

export default function App() {
  return (
    <div className="flex h-full">
      <NavRail />
      <div className="flex-1 min-w-0 overflow-auto">
        <Routes>
          <Route path="/" element={<SearchPage />} />
          <Route path="/heimildir" element={<CatalogPage />} />
          <Route path="/domur/:id" element={<DocumentPage />} />
        </Routes>
      </div>
    </div>
  );
}
