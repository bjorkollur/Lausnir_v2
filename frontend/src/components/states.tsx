import { ApiError } from "../api/client";

export function ResultsSkeleton() {
  return (
    <div className="py-4 space-y-4" aria-label="Hleð…">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="h-20 rounded bg-[var(--border)] animate-pulse" />
      ))}
    </div>
  );
}

export function EmptyState() {
  return (
    <p className="py-10 text-center text-[var(--ink-soft)]">
      Engar niðurstöður. Prófaðu að víkka leitina.
    </p>
  );
}

export function ErrorState({ error }: { error: unknown }) {
  const msg =
    error instanceof ApiError && error.status === 400
      ? error.message
      : "Eitthvað fór úrskeiðis. Reyndu aftur.";
  return <p className="py-10 text-center text-red-600">{msg}</p>;
}
