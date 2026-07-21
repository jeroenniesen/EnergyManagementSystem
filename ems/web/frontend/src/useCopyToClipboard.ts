import { useCallback, useEffect, useRef, useState } from "react";

// Shared "copy to clipboard" behaviour, hoisted out of Admin.tsx's copyMintedUrl and
// AccountTokens.tsx's copyMinted (near-verbatim copies): write `text`, flip `copied` true for the
// button's "Copy" → "Copied" swap, then auto-reset it back to false after `resetAfterMs` so the
// next copy (of a newly minted invite/token) starts from "Copy" again without the caller having to
// remember to clear it. Clipboard-unavailable (permissions/insecure context) fails silently — both
// callers already render the raw value in a selectable input as the fallback.
export function useCopyToClipboard(resetAfterMs = 2000) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const copy = useCallback(
    async (text: string) => {
      try {
        await navigator.clipboard.writeText(text);
        setCopied(true);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setCopied(false), resetAfterMs);
      } catch {
        /* clipboard unavailable — the selectable input next to the button is the fallback */
      }
    },
    [resetAfterMs],
  );

  return { copied, copy };
}
