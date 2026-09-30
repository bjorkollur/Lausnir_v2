import { useEffect } from "react";

const APP = "Lausnir";

/** Every view used to share one <title>, "Lausnir – Leit", so twenty open tabs
 *  and the browser history read as twenty copies of the same page, and a screen
 *  reader announced nothing on navigation. WCAG 2.4.2 (Page Titled). */
export function useDocumentTitle(title: string | null | undefined) {
  useEffect(() => {
    document.title = title ? `${title} – ${APP}` : `${APP} – Íslenskar réttarheimildir`;
  }, [title]);
}
