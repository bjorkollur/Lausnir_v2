import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { MoonIcon, SunIcon } from "@phosphor-icons/react";

/** Four destinations do not earn an 80px vertical rail, and the results list
 *  plus its facets want every pixel of width. So: one slim bar, text labels,
 *  no icons pretending to be a taxonomy. */

const LINKS = [
  { to: "/", label: "Leit", end: true },
  { to: "/lagasafn", label: "Lagasafn", end: false },
  { to: "/heimildir", label: "Heimildir", end: false },
  { to: "/bokasafn", label: "Bókasafn", end: false },
];

type Theme = "light" | "dark";

function useTheme(): [Theme | null, (t: Theme | null) => void] {
  const [theme, setTheme] = useState<Theme | null>(() => {
    try {
      const stored = localStorage.getItem("lausnir-theme");
      return stored === "light" || stored === "dark" ? stored : null;
    } catch {
      return null;
    }
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme) root.setAttribute("data-theme", theme);
    else root.removeAttribute("data-theme");
    try {
      if (theme) localStorage.setItem("lausnir-theme", theme);
      else localStorage.removeItem("lausnir-theme");
    } catch {
      /* private mode: the attribute still applies for this session */
    }
  }, [theme]);

  return [theme, setTheme];
}

function linkClass({ isActive }: { isActive: boolean }) {
  return [
    "relative shrink-0 px-1 py-1 text-meta transition-colors",
    isActive
      ? "text-ink after:absolute after:inset-x-0 after:-bottom-[13px] after:h-px after:bg-ink"
      : "text-ink-soft hover:text-ink",
  ].join(" ");
}

export function TopNav() {
  const [theme, setTheme] = useTheme();
  const isDark =
    theme === "dark" ||
    (theme === null &&
      typeof window !== "undefined" &&
      window.matchMedia?.("(prefers-color-scheme: dark)").matches);

  return (
    <header className="sticky top-0 z-30 border-b border-border bg-surface">
      {/* Every page puts five nav controls and a toolbar ahead of the results.
          Keyboard users get one key to step over them. */}
      <a
        href="#efni"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:rounded focus:bg-cta focus:px-3 focus:py-2 focus:text-meta focus:text-cta-ink"
      >
        Fara beint í efnið
      </a>
      <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-4 px-4 sm:gap-8 sm:px-6">
        <NavLink to="/" end className="shrink-0 font-serif text-heading text-ink" aria-label="Lausnir, forsíða">
          Lausnir
        </NavLink>

        <nav aria-label="Aðalleiðsögn" className="flex min-w-0 items-center gap-4 overflow-x-auto sm:gap-6 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {LINKS.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.end} className={linkClass}>
              {l.label}
            </NavLink>
          ))}
        </nav>

        <button
          type="button"
          onClick={() => setTheme(isDark ? "light" : "dark")}
          className="ml-auto grid h-8 w-8 place-items-center rounded text-ink-soft hover:bg-surface-sunken hover:text-ink"
          aria-label={isDark ? "Skipta í ljóst þema" : "Skipta í dökkt þema"}
        >
          {isDark ? <SunIcon size={17} aria-hidden /> : <MoonIcon size={17} aria-hidden />}
        </button>
      </div>
    </header>
  );
}
