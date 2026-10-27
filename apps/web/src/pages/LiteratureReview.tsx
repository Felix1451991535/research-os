import { useState } from "react";
import { useParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { literature, useLiteratureReview, usePrisma } from "@/api/knowledge";
import type { LiteratureReview } from "@/api/types";
import { PageHeader } from "@/components/layout/AppShell";
import { QueryView, ErrorState } from "@/components/layout/state";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { newKey } from "@/lib/utils";

const DECISIONS = ["include", "exclude", "maybe"] as const;

export function LiteratureReviewPage() {
  const { t } = useTranslation();
  const { reviewId = "" } = useParams(); const review = useLiteratureReview(reviewId); const prisma = usePrisma(reviewId);
  return <div className="grid gap-6 [&_button]:min-h-11 [&_input]:min-h-11 [&_select]:min-h-11"><PageHeader title={t("review.title")} />
    <QueryView q={review}>{r => <section className="grid gap-3"><h2>{r.title}</h2><p>{r.question}</p><pre className="whitespace-pre-wrap">{JSON.stringify(r.criteria, null, 2)}</pre>
      {!r.items.length && <p>{t("review.noCandidates", { id: r.id })}</p>}
      {r.items.map(item => <Screening key={`${reviewId}:${item.candidate.id}`} reviewId={reviewId} item={item} />)}
    </section>}</QueryView>
    <section><h2>{t("review.prismaTitle")}</h2><p>{t("review.prismaIntro")}</p>
      <QueryView q={prisma}>{data => <dl>{Object.entries(data).map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd className="whitespace-pre-wrap">{typeof value === "object" ? JSON.stringify(value, null, 2) : String(value)}</dd></div>)}</dl>}</QueryView>
    </section>
  </div>;
}
function Screening({ reviewId, item }: { reviewId: string; item: LiteratureReview["items"][number] }) {
  const { t } = useTranslation();
  const qc = useQueryClient(); const [stage, setStage] = useState("title_abstract"); const [decision, setDecision] = useState("include"); const [reason, setReason] = useState("");
  const eligible = stage !== "full_text" || item.screening.title_abstract?.decision === "include";
  const mutation = useMutation({ mutationFn: () => literature.decide(reviewId, { candidate_id: item.candidate.id, stage, decision, reason: reason.trim() }, newKey()), onSuccess: async () => {
    await Promise.all([qc.invalidateQueries({ queryKey: ["literature-review", reviewId] }), qc.invalidateQueries({ queryKey: ["prisma", reviewId] })]);
  } });
  return <form className="grid gap-3" onSubmit={e => { e.preventDefault(); if (eligible && reason.trim().length >= 3 && window.confirm(t("review.confirmDecision"))) mutation.mutate(); }}>
    <h3>{item.candidate.record.title || item.candidate.id}</h3><p>{item.candidate.record.abstract}</p>
    {Object.entries(item.screening).map(([s, state]) => <p key={s}>{t("review.stageState", { stage: s, decision: state.decision ?? t("review.undecided"), suggestion: state.suggestion ?? t("review.noSuggestion") })}</p>)}
    <label>{t("review.stage")}<select className="block" value={stage} disabled={mutation.isPending} onChange={e => setStage(e.target.value)}><option value="title_abstract">{t("review.stageTitleAbstract")}</option><option value="full_text">{t("review.stageFullText")}</option></select></label>
    <label>{t("review.decision")}<select className="block" value={decision} disabled={mutation.isPending} onChange={e => setDecision(e.target.value)}>{DECISIONS.map(d => <option key={d} value={d}>{t(`review.decision_${d}`)}</option>)}</select></label>
    <Field label={t("review.reason")} htmlFor={`reason-${item.candidate.id}`}><Input id={`reason-${item.candidate.id}`} required minLength={3} maxLength={500} value={reason} disabled={mutation.isPending} onChange={e => setReason(e.target.value)} /></Field>
    {!eligible && <p>{t("review.notEligible")}</p>}
    {mutation.error && <ErrorState error={mutation.error} />}{mutation.isSuccess && <p role="status">{t("review.recorded")}</p>}
    <Button type="submit" disabled={mutation.isPending || !eligible || reason.trim().length < 3}>{t("review.record")}</Button>
  </form>;
}
