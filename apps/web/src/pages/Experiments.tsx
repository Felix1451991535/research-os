import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { useTranslation } from "react-i18next";
import { useCreateExperimentPlan, useExperimentPlans, useExperimentTemplates } from "@/api/queries";
import type { ExperimentTemplate } from "@/api/types";
import { PageHeader } from "@/components/layout/AppShell";
import { EmptyState, ErrorState, QueryView } from "@/components/layout/state";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/input";
import { JsonDetails, parseObject } from "@/components/ExecutionDetails";
import { newKey } from "@/lib/utils";

export function ExperimentsPage() {
  const { t } = useTranslation();
  const templates = useExperimentTemplates(); const plans = useExperimentPlans();
  const create = useCreateExperimentPlan(); const navigate = useNavigate();
  const [selected, setSelected] = useState<ExperimentTemplate>();
  const [title, setTitle] = useState(""); const [content, setContent] = useState("");
  const [error, setError] = useState<unknown>();
  const [intentId, setIntentId] = useState(newKey);
  function choose(t: ExperimentTemplate) {
    setSelected(t); setIntentId(newKey()); setError(undefined);
    setContent(JSON.stringify({ hypothesis: "", template: { id: t.id, hash: t.hash }, params: {}, metrics: [], limits: { max_minutes: 5 } }, null, 2));
  }
  async function submit() {
    try {
      setError(undefined); const body = parseObject(content);
      if (!title.trim()) throw new Error(t("experiments.needTitle"));
      if (typeof body.hypothesis !== "string" || body.hypothesis.trim().length < 3) throw new Error(t("experiments.needHypothesis"));
      if (!Array.isArray(body.metrics) || !body.metrics.length || body.metrics.some(m => typeof m !== "string" || !m.trim())) throw new Error(t("experiments.needMetric"));
      if (!selected) return;
      const plan = await create.mutateAsync({ title: title.trim(), content: { ...body, template: { id: selected.id, hash: selected.hash } }, intentId });
      navigate(`/experiments/${encodeURIComponent(plan.plan_id)}/${plan.revision}`);
    } catch (e) { setError(e); }
  }
  return <div className="grid gap-6 [&_button]:min-h-11">
    <PageHeader title={t("experiments.title")} description={t("experiments.description")} />
    <section className="grid gap-3" aria-label={t("experiments.templates")}><h2 className="text-title">{t("experiments.templates")}</h2>
      <QueryView q={templates}>{items => items.length ? items.map(tpl => <div key={tpl.id} className="rounded-md border p-3">
        <h3>{tpl.id} · {tpl.status}</h3><p>{tpl.description}</p>
        <Button size="touch" disabled={tpl.status !== "reviewed" || create.isPending} onClick={() => choose(tpl)}>{t("experiments.use", { id: tpl.id })}</Button>
      </div>) : <EmptyState title={t("experiments.noTemplates")} body={t("experiments.noTemplatesBody")} />}</QueryView>
    </section>
    {selected && <form className="grid gap-3" onSubmit={e => { e.preventDefault(); void submit(); }}>
      <h2 className="text-title">{t("experiments.launchFrom", { id: selected.id })}</h2>
      <p>{t("experiments.launchIntro")}</p>
      <Field label={t("experiments.fieldTitle")} htmlFor="plan-title"><Input id="plan-title" className="h-11" maxLength={200} value={title} disabled={create.isPending} onChange={e => { setTitle(e.target.value); setIntentId(newKey()); }} /></Field>
      <Field label={t("experiments.content")} htmlFor="plan-content"><Textarea id="plan-content" value={content} disabled={create.isPending} onChange={e => { setContent(e.target.value); setIntentId(newKey()); }} /></Field>
      <details><summary className="min-h-11 cursor-pointer">{t("experiments.schema")}</summary><JsonDetails value={selected.params_schema} /></details>
      {error ? <ErrorState error={error} /> : null}
      <Button size="touch" type="submit" disabled={create.isPending}>{t("experiments.createPlan")}</Button>
    </form>}
    <section className="grid gap-3"><h2 className="text-title">{t("experiments.plans")}</h2><QueryView q={plans}>{items => items.length ? <ul>{items.map(p => <li key={p.id}>
      <Button asChild size="touch" variant="link"><Link to={`/experiments/${encodeURIComponent(p.id)}/${p.latest_revision}`}>{t("experiments.planLink", { title: p.title, revision: p.latest_revision })}</Link></Button>
    </li>)}</ul> : <EmptyState title={t("experiments.noPlans")} body={t("experiments.noPlansBody")} />}</QueryView></section>
  </div>;
}
