// Flags user-visible English left in source instead of t() (ADR-221): JSX text, UI-text attributes, string branches
// inside JSX expressions, and literal messages passed to toast / window.confirm / Error. Uses the TypeScript parser.
import { readdirSync, readFileSync } from "node:fs";
import { join, relative } from "node:path";
import ts from "typescript";
import { expect, it } from "vitest";

const SRC = join(__dirname, "..");
const UI_ATTRS = new Set(["title", "placeholder", "aria-label", "alt", "label", "description", "body", "confirmLabel", "hint"]);
const WORDS = /[A-Za-z]{2,}/;
/** Proper nouns, code identifiers and protocol values that are the same in every language. */
const ALLOWED_TEXT = [/^Research OS$/];
const ALLOWED_WORD = [/^doi:$/, /^[a-z]+(_[a-z]+)+$/, /^test$/];
/** A line marked with this comment holds developer-only text (never shown to the user). */
const EXEMPT = "i18n-exempt";
const MESSAGE_CALLS = /^(toast(\.(success|error|message|warning|info))?|window\.confirm|Error)$/;

function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = join(dir, e.name);
    if (e.isDirectory()) return e.name === "test" ? [] : files(p);
    return /\.tsx?$/.test(e.name) && !/\.test\.tsx?$/.test(e.name) && !e.name.endsWith(".d.ts") ? [p] : [];
  });
}

function literalText(n: ts.Node): string | null {
  if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) return n.text;
  if (ts.isTemplateExpression(n)) return [n.head.text, ...n.templateSpans.map((s) => s.literal.text)].join(" ");
  return null;
}

/** String literals reachable from a JSX expression through ?:, ??, || and && only (not through calls or classNames). */
function branches(n: ts.Node): ts.Node[] {
  if (ts.isParenthesizedExpression(n)) return branches(n.expression);
  if (ts.isConditionalExpression(n)) return [...branches(n.whenTrue), ...branches(n.whenFalse)];
  if (ts.isBinaryExpression(n) && [ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.BarBarToken,
    ts.SyntaxKind.AmpersandAmpersandToken].includes(n.operatorToken.kind)) return [...branches(n.left), ...branches(n.right)];
  return [n];
}

function attributeTexts(n: ts.JsxAttribute, src: ts.SourceFile): ts.Node[] {
  const init = n.initializer;
  if (!init || !UI_ATTRS.has(n.name.getText(src))) return [];
  if (!ts.isJsxExpression(init)) return [init];
  return init.expression ? branches(init.expression) : [];
}

/** Expressions under `n` whose literal text is shown to the user. */
function candidates(n: ts.Node, src: ts.SourceFile): ts.Node[] {
  if (ts.isJsxAttribute(n)) return attributeTexts(n, src);
  if (ts.isJsxExpression(n)) return n.expression && !ts.isJsxAttribute(n.parent) ? branches(n.expression) : [];
  const call = ts.isCallExpression(n) || ts.isNewExpression(n);
  const first = call && MESSAGE_CALLS.test(n.expression.getText(src)) ? n.arguments?.[0] : undefined;
  return first ? [first] : [];
}

function flagged(text: string): boolean {
  const s = text.replace(/\s+/g, " ").trim();
  if (!WORDS.test(s) || ALLOWED_TEXT.some((re) => re.test(s))) return false;
  return (s.match(/[A-Za-z_:]+/g) ?? []).some((w) => WORDS.test(w) && !ALLOWED_WORD.some((re) => re.test(w)));
}

function scan(file: string, code: string = readFileSync(file, "utf8")): string[] {
  const kind = file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const src = ts.createSourceFile(file, code, ts.ScriptTarget.Latest, true, kind);
  const lines = code.split("\n");
  const out: string[] = [];
  const report = (n: ts.Node, text: string) => {
    const { line } = src.getLineAndCharacterOfPosition(n.getStart());
    if (lines[line]?.includes(EXEMPT)) return;
    out.push(`${relative(SRC, file)}:${line + 1}: ${text.replace(/\s+/g, " ").trim()}`);
  };
  const visit = (n: ts.Node) => {
    if (ts.isJsxText(n)) { if (flagged(n.text)) report(n, n.text); }
    else for (const e of candidates(n, src)) { const text = literalText(e); if (text !== null && flagged(text)) report(e, text); }
    ts.forEachChild(n, visit);
  };
  visit(src);
  return out;
}

it("leaves no hard-coded English UI text outside the locale files", () => {
  const hits = files(SRC).flatMap((f) => scan(f));
  expect(hits).toEqual([]);
});

it("the scanner itself catches the patterns it is meant to catch", () => {
  const code = `const a = <p title="Hello there">Plain words</p>; const b = <p>{x ? "Yes indeed" : t("k")}</p>;
    toast.success("Saved it"); throw new Error(\`Bad \${v} input\`);
    throw new Error("dev only"); // i18n-exempt
    const ok = <p className="text-body" title={t("k")}>{"search_val"} Research OS {n}</p>;`;
  const hits = scan(join(SRC, "probe.tsx"), code).map((h) => h.slice(h.indexOf(": ") + 2));
  expect(hits).toEqual(["Hello there", "Plain words", "Yes indeed", "Saved it", "Bad input"]);
});
