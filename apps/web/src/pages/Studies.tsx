import { useState } from "react";
import { useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useCreateStudy } from "@/api/queries";
import { PageHeader } from "@/components/layout/AppShell";
import { ErrorState } from "@/components/layout/state";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/input";
import { parseObject } from "@/components/ExecutionDetails";
import { newKey } from "@/lib/utils";

export function StudiesPage() {
  const { t } = useTranslation();
  const create = useCreateStudy(); const navigate = useNavigate();
  const [id, setId] = useState(""); const [plan, setPlan] = useState(""); const [revision, setRevision] = useState("1");
  const [protocol, setProtocol] = useState(JSON.stringify({ objective: "", factors: [], chance: null, seeds_screen: 2, center_points: 2,
    search: { sampler: "qmc", max_trials: 30, batch: 5, patience_batches: 2 }, confirm: { top: 3, seeds: 5 }, direction: "max" }, null, 2));
  const [error, setError] = useState<unknown>(); const [intentId, setIntentId] = useState(newKey);
  async function submit() {
    try {
      setError(undefined); const body = parseObject(protocol); const rev = Number(revision);
      if (!plan.trim() || !Number.isSafeInteger(rev) || rev < 1) throw new Error(t("studies.needPlan"));
      if (typeof body.objective !== "string" || !body.objective.trim()) throw new Error(t("studies.needObjective"));
      const s = await create.mutateAsync({ plan_id: plan.trim(), revision: rev, protocol: body, intentId });
      navigate(`/studies/${encodeURIComponent(s.id)}`);
    } catch (e) { setError(e); }
  }
  return <div className="grid gap-6 [&_button]:min-h-11"><PageHeader title={t("studies.title")} description={t("studies.description")} />
    <form className="grid gap-3" onSubmit={e => { e.preventDefault(); if (id.trim()) navigate(`/studies/${encodeURIComponent(id.trim())}`); }}>
      <Field label={t("studies.studyId")} htmlFor="study-id" hint={t("studies.studyIdHint")}><Input className="h-11" id="study-id" value={id} onChange={e => setId(e.target.value)} /></Field>
      <Button size="touch" type="submit" disabled={!id.trim()}>{t("studies.open")}</Button>
    </form>
    <form className="grid gap-3" onSubmit={e => { e.preventDefault(); void submit(); }}>
      <h2 className="text-title">{t("studies.startTitle")}</h2><p>{t("studies.startIntro")}</p>
      <Field label={t("studies.planId")} htmlFor="study-plan"><Input className="h-11" id="study-plan" value={plan} disabled={create.isPending} onChange={e => { setPlan(e.target.value); setIntentId(newKey()); }} /></Field>
      <Field label={t("studies.revision")} htmlFor="study-revision"><Input className="h-11" id="study-revision" type="number" min="1" step="1" value={revision} disabled={create.isPending} onChange={e => { setRevision(e.target.value); setIntentId(newKey()); }} /></Field>
      <Field label={t("studies.protocol")} htmlFor="study-protocol"><Textarea id="study-protocol" value={protocol} disabled={create.isPending} onChange={e => { setProtocol(e.target.value); setIntentId(newKey()); }} /></Field>
      {error ? <ErrorState error={error} /> : null}<Button size="touch" type="submit" disabled={create.isPending}>{t("studies.start")}</Button>
    </form>
  </div>;
}
