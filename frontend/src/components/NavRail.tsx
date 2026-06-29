import { NavLink } from "react-router-dom";

const item = "flex flex-col items-center gap-1 py-3 text-xs text-slate-500 hover:text-indigo-600";
const active = "text-indigo-600";

function cls(isActive: boolean) {
  return `${item} ${isActive ? active : ""}`;
}

export function NavRail() {
  return (
    <nav className="w-20 shrink-0 border-r border-slate-200 bg-white flex flex-col items-center py-3">
      <div className="mb-4 font-bold text-indigo-600 text-lg">L</div>
      <NavLink to="/" className={({ isActive }) => cls(isActive)} end>
        <span aria-hidden>⌕</span>Leit
      </NavLink>
      <NavLink to="/lagasafn" className={({ isActive }) => cls(isActive)}>
        <span aria-hidden>📜</span>Lagasafn
      </NavLink>
      <NavLink to="/heimildir" className={({ isActive }) => cls(isActive)}>
        <span aria-hidden>⚖</span>Heimildir
      </NavLink>
      <NavLink to="/bokasafn" className={({ isActive }) => cls(isActive)}>
        <span aria-hidden>📚</span>Bókasafn
      </NavLink>
      <div className="mt-auto text-xs text-slate-300">👤</div>
    </nav>
  );
}
