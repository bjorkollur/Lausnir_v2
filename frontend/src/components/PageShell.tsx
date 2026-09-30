import type { ReactNode } from "react";
import { Breadcrumbs, type Crumb } from "./Breadcrumbs";
import { useDocumentTitle } from "../lib/useDocumentTitle";

/** Every full-page view shares one container, so the left edge of a heading
 *  lines up with the left edge of the nav above it. Before this the nav used
 *  max-w-[1400px] px-6 while each page picked its own p-4/p-6 and max-w-3xl,
 *  which left the content hugging the left edge with up to 1200px of empty
 *  canvas beside it. It also names the page in the browser tab. */
export function PageShell({
  title,
  subtitle,
  breadcrumbs,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  breadcrumbs?: Crumb[];
  children: ReactNode;
}) {
  useDocumentTitle(title);
  return (
    <div className="mx-auto max-w-[1400px] px-4 py-8 sm:px-6">
      <header className="mb-7">
        {breadcrumbs && <Breadcrumbs items={breadcrumbs} />}
        <h1 className="font-serif text-title text-ink">{title}</h1>
        {subtitle && <p className="mt-1 text-meta text-ink-soft">{subtitle}</p>}
      </header>
      {children}
    </div>
  );
}
