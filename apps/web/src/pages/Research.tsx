// Cited Q&A (M1, functional; polish deferred). Shows exactly what is supported, what was dropped and why.
import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { codeLabel } from "@/i18n/taskStages";
import { describeError } from "@/api/client";
import { useAnswers, useAsk, type Answer } from "@/api/knowledge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/layout/AppShell";
import { QueryView } from "@/components/layout/state";
import { newKey } from "@/lib/utils";

function AnswerCard({ a }: { a: Answer }) {
  const { t } = useTranslation();
  const byLabel = Object.fromEntries(a.passages.map((p) => [p.label, p]));
  return (
    <Card className="grid gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{a.question}</span>
        <Badge tone={a.status === "answered" ? "success" : "warning"}>{a.status.replace("_", " ")}</Badge>
        {(a.labels ?? []).map((l) => <Badge key={l}>{l}</Badge>)}</div>
      {a.reason ? <p className="text-body-sm text-muted-foreground">{a.reason}</p> : null}
      {a.sentences.map((s, i) => (
        <p key={i} className="text-body">{s.text}{" "}
          {s.evidence.map((l) => byLabel[l] ? (
            <a key={l} className="text-caption text-primary" title={byLabel[l].text}
              href={`/api/v1/documents/${byLabel[l].version_id}/file#page=${byLabel[l].page_index + 1}`} target="_blank" rel="noreferrer">
              {t("research.pdfPage", { title: byLabel[l].title, page: byLabel[l].page_index + 1 })}</a>) : null)}
          <span className="ml-1 text-caption text-muted-foreground">({codeLabel("sentenceKinds", s.kind)})</span></p>
      ))}
      {a.dropped.length ? <details className="text-body-sm text-muted-foreground"><summary>{t("research.removed", { count: a.dropped.length })}</summary>
        <ul className="list-disc pl-5">{a.dropped.map((d, i) => <li key={i}>{d.text} — {d.reason}</li>)}</ul></details> : null}
    </Card>
  );
}

export function ResearchPage() {
  const { t } = useTranslation();
  const answers = useAnswers();
  const ask = useAsk();
  const [question, setQuestion] = useState("");
  const [intentId, setIntentId] = useState(newKey);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    ask.mutate({ question, force_top: false, intentId }, {
      onSuccess: () => { setQuestion(""); setIntentId(newKey()); },
      onError: (err) => toast.error(describeError(err)),
    });
  };
  return (
    <>
      <PageHeader title={t("research.title")} description={t("research.description")} />
      <form onSubmit={submit} className="mb-4 flex gap-2">
        <Input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder={t("research.placeholder")} minLength={3} required />
        <Button type="submit" loading={ask.isPending}>{t("research.ask")}</Button>
      </form>
      <QueryView q={answers}>{(list) => <div className="grid gap-3">{list.map((a) => <AnswerCard key={a.id} a={a} />)}</div>}</QueryView>
    </>
  );
}
