import { useLayoutEffect, type RefObject } from "react";
import { useLocation, useNavigationType } from "react-router-dom";

/** Scroll positions by history entry. In memory only: a reload starts at the
 *  top, which is what a reload is for. */
const positions = new Map<string, number>();

/** Browser-style scroll restoration for an inner scroll container.
 *
 *  The app never scrolls the window: the results list, the reader and every
 *  other page scroll in their own overflow container, and the browser's own
 *  restoration only knows about the window. So returning from a judgment put
 *  the reader back at result 1 of the list they had worked through, and
 *  following a link from halfway down one page opened the next one halfway
 *  down as well.
 *
 *  A new entry (link, search) starts at the top. Back and forward return to
 *  where that entry was left. */
export function useScrollMemory(ref: RefObject<HTMLElement | null>, id: string) {
  const location = useLocation();
  const navType = useNavigationType();
  const key = `${id}:${location.key}`;

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;

    const saved = navType === "POP" ? positions.get(key) : undefined;
    let frame = 0;
    if (saved === undefined) {
      el.scrollTop = 0;
    } else {
      // Content can still be arriving (cached pages render at once, lazy
      // sections a frame later), so keep trying briefly until it fits.
      let tries = 0;
      const apply = () => {
        el.scrollTop = saved;
        if (Math.abs(el.scrollTop - saved) > 1 && tries++ < 40) {
          frame = requestAnimationFrame(apply);
        }
      };
      apply();
    }

    const onScroll = () => positions.set(key, el.scrollTop);
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      el.removeEventListener("scroll", onScroll);
    };
    // navType changes only together with the location key, so it never
    // re-runs this on its own and resets a position the reader scrolled to.
  }, [key, navType, ref]);
}
