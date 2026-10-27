import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";
import { currentLanguage } from "@/i18n";

// Teach tailwind-merge our UI type scale; otherwise `text-caption` is mistaken for a color class and dropped
// when combined with e.g. `text-info` (bug found via M0 screenshots).
const twMerge = extendTailwindMerge({
  extend: { classGroups: { "font-size": [{ text: ["caption", "body-sm", "body", "title", "heading", "display"] }] } },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

const rtfs = new Map<string, Intl.RelativeTimeFormat>();
function rtfFor(lng: string): Intl.RelativeTimeFormat {
  let f = rtfs.get(lng);
  if (!f) { f = new Intl.RelativeTimeFormat(lng, { numeric: "auto" }); rtfs.set(lng, f); }
  return f;
}

/** "3 minutes ago" style in the UI language; falls back to the locale date for older items. */
export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const s = Math.round((t - now) / 1000);
  const abs = Math.abs(s);
  const rtf = rtfFor(currentLanguage());
  if (abs < 45) return rtf.format(s, "second");
  if (abs < 2700) return rtf.format(Math.round(s / 60), "minute");
  if (abs < 64800) return rtf.format(Math.round(s / 3600), "hour");
  if (abs < 604800) return rtf.format(Math.round(s / 86400), "day");
  return formatDate(t);
}

/** Calendar date in the UI language. */
export function formatDate(t: number | string | Date): string {
  return new Date(t).toLocaleDateString(currentLanguage());
}

/** Date and time in the UI language. */
export function formatDateTime(t: number | string | Date): string {
  return new Date(t).toLocaleString(currentLanguage());
}

/** Clock time in the UI language. */
export function formatTime(t: number | string | Date): string {
  return new Date(t).toLocaleTimeString(currentLanguage());
}

export function newKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

/** Numbers for reading, not for re-computation (UI language via Intl): integers as-is (grouped), others to 4
 * significant digits. */
export function formatNumber(n: number): string {
  if (!Number.isFinite(n)) return String(n);
  if (Number.isInteger(n)) return n.toLocaleString(currentLanguage());
  const abs = Math.abs(n);
  if (abs !== 0 && (abs < 1e-3 || abs >= 1e6)) return n.toExponential(3);  // locale-neutral scientific form
  return new Intl.NumberFormat(currentLanguage(), { maximumSignificantDigits: 4 }).format(n);
}

/** "split_hash" -> "Split hash" */
export function humanize(key: string): string {
  const s = key.replace(/[_-]+/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : key;
}
