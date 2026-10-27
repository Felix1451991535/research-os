import { describe, expect, it } from "vitest";
import { detectLanguage, fromNavigator } from "./detect";

describe("UI language detection", () => {
  it("first run follows the browser: zh* -> zh-CN, anything else -> en", () => {
    expect(fromNavigator(["zh-CN"])).toBe("zh-CN");
    expect(fromNavigator(["zh-TW", "en"])).toBe("zh-CN");
    expect(fromNavigator(["zh"])).toBe("zh-CN");
    expect(fromNavigator(["en-GB", "zh-CN"])).toBe("en");
    expect(fromNavigator(["de-DE"])).toBe("en");
    expect(fromNavigator([])).toBe("en");
  });

  it("a stored choice wins over the browser; an invalid stored value is ignored", () => {
    expect(detectLanguage("en", ["zh-CN"])).toBe("en");
    expect(detectLanguage("zh-CN", ["en-US"])).toBe("zh-CN");
    expect(detectLanguage("fr", ["zh-CN"])).toBe("zh-CN");
    expect(detectLanguage(null, ["en-US"])).toBe("en");
  });
});
