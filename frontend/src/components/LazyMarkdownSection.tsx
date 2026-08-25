import { useEffect, useRef, useState } from "react";
import { Markdown } from "./Markdown";

/** Rough px-per-word estimate for the placeholder height, so the scrollbar
 * doesn't jump when a section swaps from placeholder to real content. */
function estimatedHeightPx(text: string): number {
  const words = text.trim().split(/\s+/).length;
  return Math.max(200, Math.round(words * 7));
}

export function LazyMarkdownSection({
  text,
  id,
  forceVisible = false,
}: {
  text: string;
  id?: string;
  forceVisible?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [intersected, setIntersected] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) setIntersected(true);
      },
      { rootMargin: "600px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  // Derived, not its own state: a search-jump can force this visible before it
  // ever intersects. Deriving it means the real content lands in the very same
  // render as the forceVisible prop flip — no extra render-cycle for a caller
  // (e.g. book search) to wait out before the DOM actually has real content.
  const visible = intersected || forceVisible;
  const heightPx = estimatedHeightPx(text);

  if (!visible) {
    return <div ref={ref} id={id} aria-hidden="true" style={{ height: heightPx }} />;
  }

  return (
    <section
      ref={ref}
      id={id}
      style={{ contentVisibility: "auto", containIntrinsicSize: `${heightPx}px` }}
    >
      <Markdown>{text}</Markdown>
    </section>
  );
}
