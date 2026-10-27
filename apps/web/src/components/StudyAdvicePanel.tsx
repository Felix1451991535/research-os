import { useTranslation } from "react-i18next";
import { useStudyAdvice } from "@/api/queries";
import type { StudyAdvice } from "@/api/types";
import { QueryView } from "@/components/layout/state";
import { Badge } from "@/components/ui/badge";

const sig = (v: number | null | undefined) => (v == null ? "—" : String(Number(v.toPrecision(3))));

/** Read-only next-experiment advice (ADR-168): nothing here launches runs; "Advance study" does. */
export function StudyAdvicePanel({ studyId, stage }: { studyId: string; stage: string }) {
  const { t } = useTranslation();
  const q = useStudyAdvice(studyId, stage);
  return <section className="grid gap-3" aria-label={t("advice.aria")}>
    <h2 className="text-title">{t("advice.title")}</h2>
    <QueryView q={q}>{a => <Advice a={a} />}</QueryView>
  </section>;
}

function Advice({ a }: { a: StudyAdvice }) {
  const { t } = useTranslation();
  return <div className="grid gap-3">
    <p role="status"><Badge tone={a.recommendation.startsWith("continue") ? "info" : "warning"}>{t("advice.recommendation")}</Badge> {a.recommendation}</p>
    <p className="text-body-sm text-muted-foreground">{a.caveat}</p>
    <h3 className="font-medium">{t("advice.stopRules")}</h3>
    <ul className="grid gap-1">{a.stop_rules.map(r => <li key={r.rule}>
      <Badge tone={r.met ? "warning" : "neutral"}>{r.met ? t("advice.met") : t("advice.notMet")}</Badge> {r.rule}: {r.detail}</li>)}</ul>
    <h3 className="font-medium">{t("advice.sensitivity")}</h3>
    <p className="text-body-sm text-muted-foreground">{a.sensitivity.basis}</p>
    {a.sensitivity.ranking.length ? <div className="overflow-auto"><table className="w-full text-left text-body-sm [&_td]:px-2 [&_td]:align-top [&_th]:whitespace-nowrap [&_th]:px-2">
      <thead><tr><th>{t("advice.term")}</th><th className="text-right">{t("advice.eta")}</th><th className="text-right">{t("advice.holm")}</th><th>{t("advice.statement")}</th></tr></thead>
      <tbody>{a.sensitivity.ranking.map(e => <tr key={e.term}><td>{e.term}</td><td className="text-right tabular-nums">{sig(e.partial_eta2)}</td>
        <td className="text-right tabular-nums">{sig(e.p_holm)}</td><td>{e.statement}</td></tr>)}</tbody></table></div>
      : <p>{t("advice.noScreen")}</p>}
    <h3 className="font-medium">{t("advice.uncertainty")}</h3>
    <p>{a.uncertainty.advice} {t("advice.sds", { seed: sig(a.uncertainty.seed_sd), config: sig(a.uncertainty.config_sd) })}</p>
    <h3 className="font-medium">{t("advice.proposed")}</h3>
    <p className="text-body-sm text-muted-foreground">{a.model.model ? t("advice.model", { model: a.model.model }) : a.model.reason ?? t("advice.noProposals")}</p>
    {a.proposals.length > 0 && <div className="overflow-auto"><table className="w-full text-left text-body-sm [&_td]:px-2 [&_td]:align-top [&_th]:whitespace-nowrap [&_th]:px-2">
      <thead><tr><th>{t("advice.parameters")}</th><th className="text-right">{t("advice.meanSd")}</th><th className="text-right">{t("advice.ei")}</th></tr></thead>
      <tbody>{a.proposals.map((p, i) => <tr key={i}>
        <td>{Object.entries(p.params).map(([k, v]) => `${k} = ${typeof v === "number" ? sig(v) : v}`).join(", ")}</td>
        <td className="text-right tabular-nums">{p.mean == null ? "—" : `${sig(p.mean)} ± ${sig(p.sd)}`}</td>
        <td className="text-right tabular-nums">{sig(p.ei)}</td></tr>)}</tbody></table></div>}
    {a.ablation && <Ablation ab={a.ablation} />}
    {(a.failure_memory.avoided_regions.length > 0 || a.failure_memory.constraints.length > 0) && <>
      <h3 className="font-medium">{t("advice.failureMemory")}</h3>
      <ul>{[...a.failure_memory.avoided_regions.map(r => t("advice.avoid", { region: r })), ...a.failure_memory.constraints].map(t => <li key={t}>{t}</li>)}</ul></>}
  </div>;
}

/** Pre-registered ablation (ADR-185): contribution of each factor of the frozen configuration, paired on seeds. */
function Ablation({ ab }: { ab: NonNullable<StudyAdvice["ablation"]> }) {
  const { t } = useTranslation();
  const show = (v: unknown) => (typeof v === "number" ? sig(v) : String(v));
  return <>
    <h3 className="font-medium">{t("advice.ablation")}</h3>
    {ab.data && <p className="text-body-sm text-muted-foreground">{ab.data}</p>}
    {(ab.results ?? []).length > 0 ? <div className="overflow-auto"><table className="w-full text-left text-body-sm [&_td]:px-2 [&_td]:align-top [&_th]:whitespace-nowrap [&_th]:px-2">
      <thead><tr><th>{t("advice.factor")}</th><th>{t("advice.frozenRef")}</th><th className="text-right">n</th><th className="text-right">{t("advice.contribution")}</th><th>{t("advice.verdict")}</th></tr></thead>
      <tbody>{(ab.results ?? []).map(r => <tr key={r.factor}><td>{r.factor}</td><td>{show(r.frozen)} → {show(r.reference)}</td>
        <td className="text-right tabular-nums">{r.n}{r.failed_runs ? t("advice.failedRuns", { count: r.failed_runs }) : ""}</td>
        <td className="text-right tabular-nums">{sig(r.mean)}{r.ci95 ? t("advice.ci", { low: sig(r.ci95[0]), high: sig(r.ci95[1]) }) : ""}</td>
        <td>{r.verdict}</td></tr>)}</tbody></table></div> : <p>{t("advice.pending")}</p>}
    {(ab.plan?.skipped ?? []).length > 0 && <ul className="text-body-sm text-muted-foreground">{(ab.plan?.skipped ?? []).map(s =>
      <li key={s.factor}>{t("advice.notAblated", { factor: s.factor, reason: s.reason })}</li>)}</ul>}
  </>;
}
