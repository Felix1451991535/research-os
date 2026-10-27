import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { literature, useSavedSearches } from "@/api/knowledge";
import type { LiteratureRun, SavedSearchInput } from "@/api/types";
import { PageHeader } from "@/components/layout/AppShell";
import { ErrorState, QueryView } from "@/components/layout/state";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/input";
import { formatDateTime, newKey } from "@/lib/utils";

const SOURCES: [string, string][] = [["lit_crossref", "Crossref"], ["lit_openalex", "OpenAlex"], ["lit_pubmed", "PubMed"], ["lit_arxiv", "arXiv"], ["lit_semantic", "Semantic Scholar"]];
export function LiteraturePage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [text, setText] = useState(""); const [sources, setSources] = useState(SOURCES.map(s => s[0]));
  const [run, setRun] = useState<LiteratureRun>(); const [selected, setSelected] = useState<string[]>([]);
  const [reviewId, setReviewId] = useState(""); const [title, setTitle] = useState(""); const [question, setQuestion] = useState("");
  const [include, setInclude] = useState(""); const [exclude, setExclude] = useState("");
  const [notice, setNotice] = useState("");
  const action = useMutation({ mutationFn: async (fn: () => Promise<void>) => { setNotice(""); await fn(); } });
  const accept = (r: LiteratureRun) => { setRun(r); setSelected([]); };
  const busy = action.isPending;
  return <div className="grid gap-6 [&_button]:min-h-11 [&_input]:min-h-11 [&_select]:min-h-11 [&_a]:min-h-11">
    <PageHeader title={t("literature.title")} description={t("literature.description")} />
    <form className="grid gap-3" onSubmit={e => { e.preventDefault(); action.mutate(async () => accept(await literature.search({ text: text.trim(), sources, page_size: 25, review_first: false }, newKey()))); }}>
      <Field label={t("literature.query")} htmlFor="lit-query"><Input id="lit-query" required maxLength={500} value={text} onChange={e => setText(e.target.value)} /></Field>
      <fieldset disabled={busy}><legend>{t("literature.sources")}</legend>{SOURCES.map(([id, label]) => <label key={id} className="inline-flex min-h-11 items-center gap-2 p-2"><input type="checkbox" checked={sources.includes(id)} onChange={e => setSources(e.target.checked ? [...sources, id] : sources.filter(s => s !== id))} />{label}</label>)}</fieldset>
      <Button type="submit" disabled={busy || !text.trim() || !sources.length}>{t("literature.search")}</Button>
    </form>
    {busy && <p role="status">{t("literature.working")}</p>}{action.error && <ErrorState error={action.error} />}{notice && <p role="status">{notice}</p>}
    {run && <section className="grid gap-3"><h2>{t("literature.runTitle", { id: run.id })}</h2>
      {Object.entries(run.per_source).map(([source, result]) => <p key={source}>{source}: {result.error ?? t("literature.returned", { count: result.returned ?? 0 })}{result.warnings?.map(w => ` · ${w}`)}</p>)}
      {!run.candidates.length && <p>{t("literature.noCandidates")}</p>}
      {run.candidates.map(c => <article key={c.id} className="grid gap-2"><label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={selected.includes(c.id)} onChange={e => setSelected(e.target.checked ? [...selected, c.id] : selected.filter(id => id !== c.id))} />{c.record.title || c.id}</label>
        <p>{c.record.year} {c.record.venue}</p><p>{c.record.abstract}</p>
        {([['references', t("literature.references")], ['cited_by', t("literature.citedBy")]] as const).map(([direction, label]) => <Button key={direction} disabled={busy} onClick={() => action.mutate(async () => accept(await literature.chain({ candidate_id: c.id, direction, page_size: 25 }, newKey())))}>{label}</Button>)}
      </article>)}
      <Button disabled={![!busy, selected.length > 0, Boolean(reviewId.trim())].every(Boolean)} onClick={() => action.mutate(async () => { await literature.addItems(reviewId.trim(), { candidate_ids: selected, search_run_id: run.id }, newKey()); setNotice(t("literature.added")); })}>{t("literature.addSelected")}</Button>
    </section>}
    <section className="grid gap-3"><h2>{t("literature.reviewsTitle")}</h2><p>{t("literature.reviewsIntro")}</p>
      <Field label={t("literature.reviewId")} htmlFor="review-id"><Input id="review-id" value={reviewId} onChange={e => setReviewId(e.target.value)} /></Field>
      {reviewId.trim() && <Link to={`/literature/reviews/${encodeURIComponent(reviewId.trim())}`}>{t("literature.openReview")}</Link>}
      <form className="grid gap-3" onSubmit={e => { e.preventDefault(); if (!window.confirm(t("literature.confirmCreate"))) return; action.mutate(async () => { const r = await literature.createReview({ title: title.trim(), question: question.trim(), criteria: { include: include.split('\n').filter(Boolean), exclude: exclude.split('\n').filter(Boolean) } }, newKey()); navigate(`/literature/reviews/${encodeURIComponent(r.id)}`); }); }}>
        <Field label={t("literature.reviewTitle")} htmlFor="review-title"><Input id="review-title" required maxLength={300} value={title} onChange={e => setTitle(e.target.value)} /></Field>
        <Field label={t("literature.question")} htmlFor="review-question"><Input id="review-question" required minLength={3} maxLength={2000} value={question} onChange={e => setQuestion(e.target.value)} /></Field>
        <Field label={t("literature.include")} htmlFor="review-include"><Input id="review-include" value={include} onChange={e => setInclude(e.target.value)} /></Field>
        <Field label={t("literature.exclude")} htmlFor="review-exclude"><Input id="review-exclude" value={exclude} onChange={e => setExclude(e.target.value)} /></Field>
        <Button type="submit" disabled={busy || !title.trim() || question.trim().length < 3}>{t("literature.createReview")}</Button>
      </form>
    </section>
    <SavedSearches text={text} sources={sources} />
  </div>;
}

const CADENCES = ["manual", "daily", "weekly"] as const;
const isCadence = (c: string): c is (typeof CADENCES)[number] => (CADENCES as readonly string[]).includes(c);

function SavedSearches({ text, sources }: { text: string; sources: string[] }) {
  const { t } = useTranslation();
  const saved = useSavedSearches();
  const [name, setName] = useState(""); const [cadence, setCadence] = useState<SavedSearchInput["cadence"]>("manual");
  const [notice, setNotice] = useState("");
  const action = useMutation({ mutationFn: async (fn: () => Promise<void>) => { setNotice(""); await fn(); } });
  const busy = action.isPending;
  return <>{busy && <p role="status">{t("literature.savedUpdating")}</p>}{action.error && <ErrorState error={action.error} />}{notice && <p role="status">{notice}</p>}
    <section className="grid gap-3"><h2>{t("literature.savedTitle")}</h2>
      <form className="grid gap-3" onSubmit={e => { e.preventDefault(); action.mutate(async () => { await literature.save({ name: name.trim(), kind: "query", query: { text: text.trim() }, sources, cadence }, newKey()); await saved.refetch(); setNotice(t("literature.saved")); }); }}>
        <p>{t("literature.savedIntro")}</p>
        <Field label={t("literature.savedName")} htmlFor="saved-name"><Input id="saved-name" required maxLength={200} value={name} onChange={e => setName(e.target.value)} /></Field>
        <label htmlFor="saved-cadence">{t("literature.cadence")}</label><select id="saved-cadence" value={cadence} onChange={e => setCadence(e.target.value as SavedSearchInput["cadence"])}>{CADENCES.map(c => <option key={c} value={c}>{t(`literature.cadence_${c}`)}</option>)}</select>
        <Button type="submit" disabled={busy || !name.trim() || !text.trim() || !sources.length}>{t("literature.saveSearch")}</Button>
      </form>
      <QueryView q={saved}>{rows => rows.length ? rows.map(s => <article key={s.id} className="grid gap-2"><h3>{s.name}</h3><p>{s.query.text ?? s.query.author} · {s.sources.join(', ')} · {isCadence(s.cadence) ? t(`literature.cadence_${s.cadence}`) : s.cadence} · {s.enabled ? t("literature.enabled") : t("literature.disabled")} · {t("literature.lastRun", { when: s.last_run_at ? formatDateTime(s.last_run_at) : t("literature.never") })}</p>
        <Button disabled={busy} onClick={() => action.mutate(async () => { await literature.enable(s.id, !s.enabled, newKey()); await saved.refetch(); })}>{s.enabled ? t("literature.disable") : t("literature.enable")}</Button>
        <Button disabled={busy} onClick={() => action.mutate(async () => { const task = await literature.run(s.id, newKey()); setNotice(t("literature.queuedTask", { id: task.id })); })}>{t("literature.runNow")}</Button>
      </article>) : <p>{t("literature.noSaved")}</p>}</QueryView><Link to="/tasks">{t("literature.tasksLink")}</Link>
    </section></>;
}
