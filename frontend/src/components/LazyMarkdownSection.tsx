import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

/** Rough px-per-word estimate for the placeholder height, so the scrollbar
 * doesn't jump when a section swaps from placeholder to real content. */
function estimatedHeightPx(text: string): number {
  const words = text.trim().split(/\s+/).length;
  return Math.max(200, Math.round(words * 7));
}

export function LazyMarkdownSection({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) setVisible(true);
      },
      { rootMargin: "600px 0px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const heightPx = estimatedHeightPx(text);

  if (!visible) {
    return <div ref={ref} aria-hidden="true" style={{ height: heightPx }} />;
  }

  return (
    <section
      ref={ref}
      style={{ contentVisibility: "auto", containIntrinsicSize: `${heightPx}px` }}
    >
      <ReactMarkdown>{text}</ReactMarkdown>
    </section>
  );
}
