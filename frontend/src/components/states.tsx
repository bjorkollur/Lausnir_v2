import type { ReactNode } from "react";
import { ApiError } from "../api/client";

/** Placeholder rows in the shape of a result, not grey slabs: title, source
 *  line and two lines of snippet, so the list does not jump when it lands. */
export function ResultsSkeleton() {
  return (
    <div role="status" className="space-y-1 py-3">
      <span className="sr-only">Sæki niðurstöður…</span>
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} aria-hidden className="animate-pulse space-y-2.5 py-5">
          <div className="h-4 w-2/5 rounded bg-surface-sunken" />
          <div className="h-3 w-1/5 rounded bg-surface-sunken" />
          <div className="h-3 w-4/5 max-w-[60ch] rounded bg-surface-sunken" />
          <div className="h-3 w-3/5 max-w-[46ch] rounded bg-surface-sunken" />
        </div>
      ))}
    </div>
  );
}

/** "Nothing found" that names what was searched and offers the ways out,
 *  instead of a one-line shrug. */
export function EmptyState({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <div className="max-w-[60ch] py-12">
      <p className="text-heading text-ink">{title}</p>
      {children && <div className="mt-3 space-y-3 text-body text-ink-soft">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg =
    error instanceof ApiError && error.status === 400
      ? error.message
      : "Ekki tókst að sækja gögnin. Athugaðu nettenginguna og reyndu aftur.";
  return (
    <div role="alert" className="py-12">
      <p className="text-body text-danger">{msg}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 h-9 rounded-md border border-border bg-surface px-4 text-meta text-ink hover:border-border-strong"
        >
          Reyna aftur
        </button>
      )}
    </div>
  );
}
