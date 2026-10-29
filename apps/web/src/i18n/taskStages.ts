// Task stages are codes the host or a plugin sets (`ctx.progress("parsing", ...)`; ADR-245). Known codes are
// translated under `taskStages`; any other stage (e.g. one a template reports) is shown as sent. The same holds for
// the other protocol codes the UI shows as labels (document kinds, answer sentence kinds; M8).
import i18n from "i18next";

export function codeLabel(group: "taskStages" | "docKinds" | "sentenceKinds", code: string): string {
  const key = `${group}.${code}`;
  return i18n.exists(key) ? String(i18n.t(key as never)) : code;
}

export const stageLabel = (code: string): string => codeLabel("taskStages", code);
