import { useState } from "react";
import { Link, useParams } from "react-router";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { describeError } from "@/api/client";
import { useAdvanceStudy, useApprove, useStudy, useTasks } from "@/api/queries";
import type { Study } from "@/api/types";
import { PageHeader } from "@/components/layout/AppShell";
import { EmptyState, ErrorState, QueryView } from "@/components/layout/state";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { StudyAdvicePanel } from "@/components/StudyAdvicePanel";
import { ApprovalReauthentication, ExperimentTasks, JsonDetails } from "@/components/ExecutionDetails";
import { newKey, formatNumber } from "@/lib/utils";

export function StudyDetailPage() {
  const { t } = useTranslation();
  const { studyId = "" } = useParams(); const q = useStudy(studyId);
  return <div className="grid gap-6 [&_button]:min-h-11"><PageHeader title={t("study.title")} />
    <QueryView q={q}>{s => <StudyResults key={`${s.id}@${s.current_revision}`} study={s} fresh={!q.isError && !q.isFetching} />}</QueryView>
  </div>;
}
function StudyResults({ study: s, fresh }: { study: Study; fresh: boolean }) {
  const { t } = useTranslation();
  const tasks = useTasks(); const advance = useAdvanceStudy(s.id); const approve = useApprove();
  const [confirm, setConfirm] = useState(false);
  const [advanceKey, setAdvanceKey] = useState(newKey);
  const [approvalKey] = useState(newKey);
  const action = useMutation({ mutationFn: async (final: boolean) => {
    if (final) await approve.mutateAsync({ subjectType: "final_eval", subjectId: `${s.plan_id}@${s.current_revision}`, intentId: approvalKey, onlineOnly: true });
    await advance.mutateAsync(advanceKey); setAdvanceKey(newKey()); setConfirm(false);
  } });
  const final = s.stage === "awaiting_final_approval";
  const terminal = s.stage === "completed" || s.stage === "stopped";
  const busy = action.isPending;
  return <div className="grid gap-4">
    <h2 className="text-title">{s.id} · {s.stage}</h2><p>{t("study.evaluated", { count: s.configs_evaluated })}</p>
    <Button asChild size="touch" variant="link"><Link to={`/experiments/${encodeURIComponent(s.plan_id)}/${s.current_revision}`}>{t("study.planLink", { plan: s.plan_id, revision: s.current_revision })}</Link></Button>
    {s.data.stop_reason && <p role="status">{t("study.stopped", { reason: s.data.stop_reason })}</p>}
    {s.data.search_stopped && <p>{t("study.searchStopped", { reason: s.data.search_stopped })}</p>}
    <details><summary className="min-h-11 cursor-pointer">{t("study.protocol")}</summary><JsonDetails value={s.protocol} /></details>
    <p>{t("study.advanceIntro")}</p>
    <Button size="touch" disabled={terminal || busy || !fresh} onClick={() => final ? setConfirm(true) : action.mutate(false)}>{final ? t("study.finalEval") : t("study.advance")}</Button>
    {action.error && <ErrorState error={action.error} />}
    <StudyAdvicePanel studyId={s.id} stage={s.stage} />
    <StudyAnalysis study={s} />
    <StudyOutcomes study={s} />
    <h2 className="text-title">{t("study.sweepRuns")}</h2><p>{t("plan.recentOnly")}</p><QueryView q={tasks}>{rows => <ExperimentTasks tasks={rows.filter(task => task.kind === "experiment.run" && task.payload.study_id === s.id)} />}</QueryView>
    <ConfirmDialog open={confirm} onOpenChange={o => { if (!busy) setConfirm(o); }} title={t("plan.finalTitle")}
      description={t("study.finalDescription", { ref: `${s.plan_id}@${s.current_revision}` })}
      error={action.error ? describeError(action.error) : undefined} confirmDisabled={!fresh || !final}
      confirmLabel={t("plan.confirmFinal")} busy={busy} onConfirm={() => { if (!busy && fresh && final) action.mutate(true); }}><ApprovalReauthentication error={action.error} /></ConfirmDialog>
  </div>;
}

function StudyAnalysis({ study: s }: { study: Study }) {
  return <><AnovaResults anova={s.data.screen?.anova} /><FailureRules screen={s.data.screen} /></>;
}
function AnovaResults({ anova: a }: { anova: NonNullable<Study["data"]["screen"]>["anova"] }) {
  const { t } = useTranslation();
  return <section className="grid gap-3"><h2 className="text-title">{t("study.anova")}</h2>
      {a ? <><p>{t("study.anovaIntro")}</p>
        {a.effects.length ? <div className="overflow-auto"><table className="w-full text-left [&_td]:px-2 [&_td]:align-top [&_th]:whitespace-nowrap [&_th]:px-2"><thead><tr><th>{t("study.term")}</th><th>{t("study.reporting")}</th><th>{t("study.holm")}</th><th>{t("study.eta")}</th><th>{t("study.statement")}</th></tr></thead>
          <tbody>{a.effects.map(e => <EffectRow key={e.term} effect={e} />)}</tbody></table></div> : <EmptyState title={t("study.noEffects")} />}
        <p>{t("study.sigma", { value: a.sigma == null ? t("common.unavailable") : formatNumber(a.sigma) })}</p><ul>{a.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        <h3>{t("study.curvature")}</h3>{a.curvature == null ? <p>{t("study.noCurvature")}</p> : <JsonDetails value={a.curvature} />}
      </> : <EmptyState title={t("study.noAnova")} body={t("study.noAnovaBody")} />}
    </section>;
}
function FailureRules({ screen }: { screen: Study["data"]["screen"] }) {
  const { t } = useTranslation();
  const rules = screen?.failure_rules;
  return <section className="grid gap-3"><h2 className="text-title">{t("study.failureRules")}</h2>
      {rules?.length ? <ul>{rules.map((r, i) => <li key={i}>{t("study.ruleLine", { rule: r.rule, failed: r.failed, support: r.support, purity: r.purity })}</li>)}</ul> : <EmptyState title={t("study.noRules")} body={t("study.noRulesBody")} />}
      {screen?.avoid?.length ? <><h3>{t("study.avoided")}</h3><ul>{screen.avoid.map(r => <li key={r}>{r}</li>)}</ul></> : null}
    </section>;
}
function StudyOutcomes({ study: s }: { study: Study }) {
  const { t } = useTranslation();
  return <>
    {s.data.surface && <section><h2>{t("study.surface")}</h2><JsonDetails value={s.data.surface} /></section>}
    {s.data.confirm && <section><h2>{t("study.confirm")}</h2><JsonDetails value={s.data.confirm} /></section>}
    {s.data.control && <section><h2>{t("study.control")}</h2><JsonDetails value={s.data.control} /></section>}
    <section><h2 className="text-title">{t("study.sealed")}</h2>
      {s.data.frozen && <><h3>{t("study.frozen")}</h3><JsonDetails value={s.data.frozen} /></>}
      {s.data.final ? <JsonDetails value={s.data.final} /> : <p>{t("study.noFinal")}</p>}
    </section>
</>;
}

function EffectRow({ effect: e }: { effect: import("@/api/types").StudyEffect }) {
  const { t } = useTranslation();
  return <tr><td>{e.term}</td><td>{e.p == null ? t("study.notEstimable") : e.reported ? t("study.reported") : t("study.notReported")}</td><td className="tabular-nums" title={e.p_holm == null ? undefined : String(e.p_holm)}>{e.p_holm == null ? t("common.unavailable") : formatNumber(e.p_holm)}</td>
    <td className="tabular-nums" title={e.partial_eta2 == null ? undefined : String(e.partial_eta2)}>{e.partial_eta2 == null ? t("common.unavailable") : formatNumber(e.partial_eta2)}</td><td>{e.statement}</td></tr>;
}
