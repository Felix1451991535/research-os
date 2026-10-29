import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { describeError } from "@/api/client";
import { useApprove, usePlanRevision, useStartExperimentRun, useTasks } from "@/api/queries";
import type { PlanRevision } from "@/api/types";
import { PageHeader } from "@/components/layout/AppShell";
import { ErrorState, QueryView } from "@/components/layout/state";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/input";
import { ApprovalReauthentication, ExperimentTasks, JsonDetails, parseObject } from "@/components/ExecutionDetails";
import { ExportResult } from "@/components/ExportResult";
import { usePlanBundle } from "@/api/writing";
import { newKey } from "@/lib/utils";

export function ExperimentPlanPage() {
  const { t } = useTranslation();
  const { planId = "", revision = "1" } = useParams();
  const q = usePlanRevision(planId, Number(revision));
  return <div className="grid gap-6 [&_button]:min-h-11"><PageHeader title={t("plan.title")} />
    <QueryView q={q}>{r => <PlanActions key={`${r.plan_id}@${r.revision}`} plan={r} fresh={!q.isError && !q.isFetching} />}</QueryView>
  </div>;
}

function PlanActions({ plan, fresh }: { plan: PlanRevision; fresh: boolean }) {
  const { t } = useTranslation();
  const tasks = useTasks(); const approve = useApprove();
  const start = useStartExperimentRun(plan.plan_id, plan.revision); const navigate = useNavigate();
  const [confirmation, setConfirmation] = useState<"plan" | "final" | null>(null);
  const [params, setParams] = useState(JSON.stringify(plan.content.params ?? {}, null, 2));
  const [seed, setSeed] = useState("0");
  const [keys] = useState(() => ({ approval: newKey(), finalApproval: newKey(), finalRun: newKey() }));
  const [trialKey, setTrialKey] = useState(newKey);
  const approved = plan.approved_by_revision != null;
  const planTasks = (tasks.data ?? []).filter(task => task.kind === "experiment.run" && task.payload.plan_id === plan.plan_id);
  const finalLocked = planTasks.some(task => task.payload.stage === "final_eval" && !["failed", "cancelled"].includes(task.state));
  const action = useMutation({ mutationFn: async (kind: "plan" | "trial" | "final") => {
    if (kind === "plan") {
      await approve.mutateAsync({ subjectType: "plan", subjectId: `${plan.plan_id}@${plan.revision}`, expectedHash: plan.content_hash, intentId: keys.approval, onlineOnly: true });
    } else {
      const parsed = parseObject(params); const n = Number(seed);
      if (!seed.trim() || !Number.isSafeInteger(n)) throw new Error(t("plan.seedInteger"));
      if (kind === "final") await approve.mutateAsync({ subjectType: "final_eval", subjectId: `${plan.plan_id}@${plan.revision}`, intentId: keys.finalApproval, onlineOnly: true });
      const task = await start.mutateAsync({ stage: kind === "final" ? "final_eval" : "trial", params: parsed, seed: n, intentId: kind === "final" ? keys.finalRun : trialKey });
      navigate(`/experiments/tasks/${encodeURIComponent(task.id)}`);
    }
    setConfirmation(null);
  } });
  const busy = action.isPending; const ready = [fresh, tasks.isSuccess, !tasks.isFetching, !busy].every(Boolean);
  return <div className="grid gap-4">
    <h2 className="text-title">{t("plan.heading", { id: plan.plan_id, revision: plan.revision })}</h2>
    <p className="wrap-anywhere">{t("plan.hash", { hash: plan.content_hash })}</p><p>{approved ? t("plan.approvedBy", { revision: plan.approved_by_revision }) : t("plan.approvalRequired")}</p>
    <JsonDetails value={plan.content} />
    <Button size="touch" disabled={approved || !fresh || busy} onClick={() => setConfirmation("plan")}>{t("plan.approve")}</Button>
    <h2 className="text-title">{t("plan.runParams")}</h2>
    <Field label={t("plan.params")} htmlFor="run-params"><Textarea id="run-params" value={params} disabled={busy} onChange={e => { setParams(e.target.value); setTrialKey(newKey()); }} /></Field>
    <Field label={t("plan.seed")} htmlFor="run-seed"><Input className="h-11" id="run-seed" type="number" step="1" value={seed} disabled={busy} onChange={e => { setSeed(e.target.value); setTrialKey(newKey()); }} /></Field>
    <Button size="touch" disabled={!approved || !ready} onClick={() => action.mutate("trial")}>{t("plan.startTrial")}</Button>
    <section className="grid gap-2"><h2 className="text-title">{t("plan.sealedTitle")}</h2>
      <p>{t("plan.sealedIntro")}</p>
      {finalLocked && <p role="status">{t("plan.finalLocked")}</p>}
      <Button size="touch" disabled={![approved, ready, !finalLocked].every(Boolean)} onClick={() => setConfirmation("final")}>{t("plan.finalEval")}</Button>
    </section>
    {action.error && <ErrorState error={action.error} />}
    <Button asChild size="touch" variant="link"><Link to="/studies">{t("plan.createStudy")}</Link></Button>
    <h2 className="text-title">{t("plan.runStatus")}</h2><p>{t("plan.recentOnly")}</p><QueryView q={tasks}>{() => <ExperimentTasks tasks={planTasks} />}</QueryView>
    <PlanBundle planId={plan.plan_id} hasRuns={planTasks.length > 0} />
    <PlanConfirmation confirmation={confirmation} setConfirmation={setConfirmation} plan={plan} busy={busy} fresh={confirmation === "plan" ? fresh : ready && !finalLocked} error={action.error} submit={kind => action.mutate(kind)} />
  </div>;
}


function PlanBundle({ planId, hasRuns }: { planId: string; hasRuns: boolean }) {
  const { t } = useTranslation();
  const bundle = usePlanBundle(planId);
  return <section className="grid gap-2"><h2 className="text-title">{t("plan.bundleTitle")}</h2>
    <p>{t("plan.bundleIntro")}</p>
    <Button size="touch" disabled={!hasRuns || bundle.isPending} onClick={() => bundle.mutate(newKey())}>{t("plan.buildBundle")}</Button>
    {bundle.error && <ErrorState error={bundle.error} />}
    {bundle.data && <ExportResult label={t("plan.bundleLabel", { runs: bundle.data.runs, failed: bundle.data.failedRuns })} result={bundle.data} />}
  </section>;
}

function PlanConfirmation({ confirmation, setConfirmation, plan, busy, fresh, error, submit }: {
  confirmation: "plan" | "final" | null; setConfirmation: (value: "plan" | "final" | null) => void;
  plan: PlanRevision; busy: boolean; fresh: boolean; error: unknown; submit: (kind: "plan" | "final") => void;
}) {
  const { t } = useTranslation();
  const ref = `${plan.plan_id}@${plan.revision}`;
  return (<ConfirmDialog open={confirmation !== null} onOpenChange={o => { if (!o && !busy) setConfirmation(null); }}
      title={confirmation === "plan" ? t("plan.approveTitle") : t("plan.finalTitle")}
      description={confirmation === "plan" ? t("plan.approveDescription", { ref, hash: plan.content_hash }) : t("plan.finalDescription", { ref })}
      error={error ? describeError(error) : undefined} confirmDisabled={!fresh}
      confirmLabel={confirmation === "plan" ? t("plan.confirmApproval") : t("plan.confirmFinal")} busy={busy}
      onConfirm={() => { if (!busy && fresh) submit(confirmation === "plan" ? "plan" : "final"); }}><ApprovalReauthentication error={error} /></ConfirmDialog>);
}
