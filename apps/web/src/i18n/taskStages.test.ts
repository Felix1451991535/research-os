import { afterEach, describe, expect, it } from "vitest";
import i18n from "@/i18n";
import { codeLabel, stageLabel } from "./taskStages";

describe("task stage codes in the UI language", () => {
  afterEach(async () => { await i18n.changeLanguage("en"); });

  it("translates known codes and shows unknown ones as sent", async () => {
    expect(stageLabel("parsing")).toBe("parsing");
    await i18n.changeLanguage("zh-CN");
    expect(stageLabel("parsing")).toBe("解析中");
    expect(stageLabel("fit")).toBe("拟合模型");
    expect(stageLabel("epoch_7")).toBe("epoch_7");
  });
});

describe("other protocol codes shown as labels", () => {
  afterEach(async () => { await i18n.changeLanguage("en"); });

  it("translates document and sentence kinds, unknown ones as sent", async () => {
    await i18n.changeLanguage("zh-CN");
    expect(codeLabel("docKinds", "manuscript")).toBe("稿件");
    expect(codeLabel("sentenceKinds", "model_inference")).toBe("模型推断");
    expect(codeLabel("docKinds", "plugin_kind")).toBe("plugin_kind");
  });
});
