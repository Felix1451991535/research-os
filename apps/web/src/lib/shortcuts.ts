// Global keyboard shortcuts. "g" then a letter navigates; "n" triggers the page's primary "new" action;
// "?" opens the shortcut sheet. Ignored while typing in inputs.
import { useEffect } from "react";

export const NAV_SHORTCUTS: Record<string, string> = { p: "/projects", l: "/library", r: "/research", t: "/tasks", s: "/settings" };

function typing(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return !!el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
}

export function useGlobalShortcuts(navigate: (to: string) => void, openHelp: () => void) {
  useEffect(() => {
    let pendingG = false;
    let timer: number | undefined;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return;
      if (pendingG) {
        pendingG = false;
        const to = NAV_SHORTCUTS[e.key];
        if (to) { e.preventDefault(); navigate(to); }
        return;
      }
      if (e.key === "g") { pendingG = true; window.clearTimeout(timer); timer = window.setTimeout(() => { pendingG = false; }, 1200); }
      else if (e.key === "?") { e.preventDefault(); openHelp(); }
      else if (e.key === "n") { e.preventDefault(); window.dispatchEvent(new CustomEvent("ros:new")); }
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); window.clearTimeout(timer); };
  }, [navigate, openHelp]);
}

/** Pages register their primary "new" action for the "n" shortcut. */
export function useNewShortcut(handler: () => void) {
  useEffect(() => {
    window.addEventListener("ros:new", handler);
    return () => window.removeEventListener("ros:new", handler);
  }, [handler]);
}
