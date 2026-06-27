import { NavLink } from "react-router-dom";

const item = "flex flex-col items-center gap-1 py-3 text-xs text-slate-500 hover:text-indigo-600";
const active = "text-indigo-600";

export function NavRail() {
  return (
    <nav className="w-20 shrink-0 border-r border-slate-200 bg-white flex flex-col items-center py-3">
      <div className="mb-4 font-bold text-indigo-600">L</div>
      <NavLink to="/" className={({ isActive }) => `${item} ${isActive ? active : ""}`} end>
        <span aria-hidden>⌂</span>Leit
      </NavLink>
      <NavLink to="/heimildir" className={({ isActive }) => `${item} ${isActive ? active : ""}`}>
        <span aria-hidden>⚖</span>Heimildir
      </NavLink>
      <div className="mt-auto text-xs text-slate-300">👤</div>
    </nav>
  );
}
