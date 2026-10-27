// UI language choice (ADR-221): a stored choice wins; otherwise the browser language decides on first run.
export const LANGUAGES = ["zh-CN", "en"] as const;
export type Language = (typeof LANGUAGES)[number];
export const STORAGE_KEY = "ros-lang";

export function isLanguage(v: unknown): v is Language {
  return typeof v === "string" && (LANGUAGES as readonly string[]).includes(v);
}

/** zh* -> zh-CN, anything else -> en. */
export function fromNavigator(langs: readonly string[]): Language {
  const first = langs.find(Boolean) ?? "";
  return first.toLowerCase().startsWith("zh") ? "zh-CN" : "en";
}

export function detectLanguage(stored: string | null, langs: readonly string[]): Language {
  return isLanguage(stored) ? stored : fromNavigator(langs);
}

function readStored(): string | null {
  try { return localStorage.getItem(STORAGE_KEY); } catch { return null; }  // storage may be disabled
}

export function initialLanguage(): Language {
  const langs = typeof navigator === "undefined" ? [] : navigator.languages?.length ? navigator.languages : [navigator.language];
  return detectLanguage(readStored(), langs);
}

export function storeLanguage(lng: Language): void {
  try { localStorage.setItem(STORAGE_KEY, lng); } catch { /* the choice then lasts for this session only */ }
}
