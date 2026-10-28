// #366 — blur/parse for a limit field. Empty or whitespace keeps the previous draft
// (the caller does not write). A comma is a decimal separator, same as a dot.
// Out of range is returned as typed so the existing field error can block Save;
// the value is never snapped to min, 0, or "unlimited".

export type NumberBlurBounds = {
  min?: number | null;
  max?: number | null;
  integer?: boolean;
};

export type NumberBlur =
  | { action: "restore" }
  | { action: "commit"; value: number }
  | { action: "out_of_range"; value: number };

const DECIMAL = /^[+-]?(?:\d+\.?\d*|\.\d+)$/;

function parseDecimal(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  const hasComma = trimmed.includes(",");
  const hasDot = trimmed.includes(".");
  if (hasComma && hasDot) return null;
  const normalised = hasComma ? trimmed.replace(/,/g, ".") : trimmed;
  if (!DECIMAL.test(normalised)) return null;
  const n = Number(normalised);
  return Number.isFinite(n) ? n : null;
}

export function resolveNumberBlur(raw: string, bounds: NumberBlurBounds = {}): NumberBlur {
  const parsed = parseDecimal(raw);
  if (parsed === null) return { action: "restore" };
  const value = bounds.integer ? Math.round(parsed) : parsed;
  const { min, max } = bounds;
  if ((min != null && value < min) || (max != null && value > max)) {
    return { action: "out_of_range", value };
  }
  return { action: "commit", value };
}

/** Wheel must not step a focused number input; the page scrolls instead. */
export function attachNumberInputWheel(el: HTMLElement): () => void {
  const onWheel = (event: WheelEvent) => {
    event.preventDefault();
    if (document.activeElement === el) el.blur();
    const scroller = document.scrollingElement;
    if (scroller) scroller.scrollBy({ top: event.deltaY, left: event.deltaX });
  };
  el.addEventListener("wheel", onWheel, { passive: false });
  return () => el.removeEventListener("wheel", onWheel);
}
