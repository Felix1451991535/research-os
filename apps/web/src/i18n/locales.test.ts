import { describe, expect, it } from "vitest";
import en from "./locales/en.json";
import zhCN from "./locales/zh-CN.json";
import { API_ERROR_KEYS } from "./apiErrors";

type Tree = { [k: string]: string | Tree };

function flatten(tree: Tree, prefix = ""): Record<string, string> {
  return Object.entries(tree).reduce<Record<string, string>>((acc, [k, v]) =>
    Object.assign(acc, typeof v === "string" ? { [prefix + k]: v } : flatten(v, `${prefix}${k}.`)), {});
}

const flatEn = flatten(en as Tree);
const flatZh = flatten(zhCN as Tree);
const placeholders = (s: string) => [...s.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1]).sort();

describe("locale resources", () => {
  it("zh-CN and en have identical key sets", () => {
    expect(Object.keys(flatZh).sort()).toEqual(Object.keys(flatEn).sort());
  });

  it("no value is empty", () => {
    for (const [lng, flat] of [["en", flatEn], ["zh-CN", flatZh]] as const) {
      const empty = Object.entries(flat).filter(([, v]) => !v.trim()).map(([k]) => `${lng}:${k}`);
      expect(empty).toEqual([]);
    }
  });

  it("each translation uses the same interpolation variables as English", () => {
    const diff = Object.keys(flatEn).filter((k) => placeholders(flatEn[k] ?? "").join() !== placeholders(flatZh[k] ?? "").join());
    expect(diff).toEqual([]);
  });

  it("every mapped backend error has a translation", () => {
    expect(API_ERROR_KEYS.filter((k) => !flatEn[`apiErrors.${k}`] || !flatZh[`apiErrors.${k}`])).toEqual([]);
  });

  it("the Chinese resource is actually translated (not a copy of English)", () => {
    const same = Object.keys(flatEn).filter((k) => flatEn[k] === flatZh[k] && /[a-z]{4,}/i.test(flatEn[k] ?? ""));
    expect(same.length / Object.keys(flatEn).length).toBeLessThan(0.05);  // only proper nouns / examples stay equal
  });
});
