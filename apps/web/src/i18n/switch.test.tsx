import { afterEach, expect, it, vi } from "vitest";
import { act } from "react";
import i18n from "@/i18n";
import { STORAGE_KEY } from "./detect";
import { api } from "@/api/client";
import { renderPage } from "@/test/pageHarness";
import { LanguageSelect, LanguageToggle } from "@/components/layout/language";
import { TooltipProvider } from "@/components/ui/misc";
import { ProjectsPage } from "@/pages/Projects";
import { formatNumber, relativeTime } from "@/lib/utils";

afterEach(async () => { vi.restoreAllMocks(); localStorage.clear(); await i18n.changeLanguage("en"); });

const page = <TooltipProvider><LanguageSelect /><LanguageToggle /><ProjectsPage /></TooltipProvider>;

it("switches the rendered UI immediately, sets <html lang> and remembers the choice", async () => {
  vi.spyOn(api, "get").mockResolvedValue([]);
  const p = await renderPage(page, "/", "/");
  try {
    expect(document.body.textContent).toContain("No projects yet");
    const select = document.getElementById("ui-language") as HTMLSelectElement;
    await act(async () => { select.value = "zh-CN"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    await p.flush();
    expect(document.body.textContent).toContain("还没有项目");
    expect(document.body.textContent).not.toContain("No projects yet");
    expect(document.documentElement.lang).toBe("zh-CN");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("zh-CN");
    expect(select.value).toBe("zh-CN");

    const toggle = document.querySelector('button[aria-label="切换语言为English"]') as HTMLButtonElement;
    await act(async () => toggle.click());
    await p.flush();
    expect(document.body.textContent).toContain("No projects yet");
    expect(document.documentElement.lang).toBe("en");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("en");
  } finally { await p.dispose(); }
});

it("formats dates and numbers with Intl in the current language", async () => {
  const now = Date.parse("2026-10-27T12:00:00Z");
  expect(relativeTime("2026-10-27T11:57:00Z", now)).toBe("3 minutes ago");
  await i18n.changeLanguage("zh-CN");
  expect(relativeTime("2026-10-27T11:57:00Z", now)).toBe("3分钟前");
  expect(formatNumber(1234567)).toBe(new Intl.NumberFormat("zh-CN").format(1234567));
});
