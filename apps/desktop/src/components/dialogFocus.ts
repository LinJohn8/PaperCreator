import type { KeyboardEvent } from "react";

const FOCUSABLE = [
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "a[href]",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

/** Keep keyboard focus inside a modal while preserving normal tab order. */
export function trapFocusIn(
  container: HTMLElement,
  event: Pick<globalThis.KeyboardEvent, "key" | "shiftKey" | "preventDefault">,
) {
  if (event.key !== "Tab") return;
  const elements = Array.from(
    container.querySelectorAll<HTMLElement>(FOCUSABLE),
  ).filter((element) => !element.hidden && element.getClientRects().length > 0);
  if (!elements.length) {
    event.preventDefault();
    return;
  }
  const first = elements[0];
  const last = elements[elements.length - 1];
  const active = document.activeElement;
  if (event.shiftKey && (active === first || !container.contains(active))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (active === last || !container.contains(active))) {
    event.preventDefault();
    first.focus();
  }
}

export function trapDialogFocus(event: KeyboardEvent<HTMLElement>) {
  trapFocusIn(event.currentTarget, event.nativeEvent);
}
