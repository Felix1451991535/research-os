// i18next setup (ADR-221). Resources are bundled JSON (no runtime fetch); keys are type-checked against en.json.
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";
import zhCN from "./locales/zh-CN.json";
import { initialLanguage, isLanguage, storeLanguage, type Language } from "./detect";

export { LANGUAGES, type Language } from "./detect";

declare module "i18next" {
  interface CustomTypeOptions { defaultNS: "translation"; resources: { translation: typeof en } }
}

function applyHtmlLang(lng: string) {
  if (typeof document !== "undefined") document.documentElement.lang = lng;
}

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, "zh-CN": { translation: zhCN } },
  lng: initialLanguage(),
  fallbackLng: "en",
  supportedLngs: ["zh-CN", "en"],
  interpolation: { escapeValue: false },  // React escapes
  returnNull: false,
  initAsync: false,
});
applyHtmlLang(i18n.language);
i18n.on("languageChanged", applyHtmlLang);

/** Switch immediately (no reload) and remember the choice on this device. */
export function setLanguage(lng: Language): void {
  storeLanguage(lng);
  void i18n.changeLanguage(lng);
}

/** Current UI language, for Intl formatting. */
export function currentLanguage(): Language {
  return isLanguage(i18n.language) ? i18n.language : "en";
}

export default i18n;
