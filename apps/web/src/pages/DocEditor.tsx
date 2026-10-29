// Doc editor (M6, functional; visual polish deferred by the owner). Plain Markdown with placeholders:
// {{cite:key}} {{evidence:id}} {{metric:run/name/split}} {{lit:text}}. The gate panel shows what blocks a final export.
import { useState } from "react";
import { Link, useParams } from "react-router";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { describeError } from "@/api/client";
import { acceptBlockedReason, useDecide, useDoc, useExport, useGates, useSaveDoc, useSuggestions, type WDoc } from "@/api/writing";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Textarea } from "@/components/ui/input";
import { PageHeader } from "@/components/layout/AppShell";
import { ErrorState, ListSkeleton } from "@/components/layout/state";
import { ExportLanguageSelect, ExportResult, type ExportLanguage } from "@/components/ExportResult";
import { newKey } from "@/lib/utils";
import { diagnosticMessage, severityLabel } from "@/i18n/gateDiagnostics";
import { codeLabel } from "@/i18n/taskStages";

const EXPORTERS: readonly (readonly [string, ParseKeys])[] = [["markdown", "doc.exporterMarkdown"], ["report_docx", "doc.exporterWord"],
  ["report_latex", "doc.exporterLatex"], ["report_slides", "doc.exporterSlides"]];
const DECIDED: Record<string, ParseKeys> = { accepted: "doc.suggestionAccepted", rejected: "doc.suggestionRejected" };

function GatePanel({ id, version }: { id: string; version: number }) {
  const { t } = useTranslation();
  const g = useGates(id, version);
  if (!g.data) return <p className="text-body-sm text-muted-foreground">{t("doc.checking")}</p>;
  const { counts, diagnostics, ok } = g.data;
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap gap-2">
        <Badge tone={ok ? "success" : "warning"}>{ok ? t("doc.ready") : t("doc.blocked")}</Badge>
        <Badge>{t("doc.citations", { count: counts.citations })}</Badge><Badge>{t("doc.metrics", { count: counts.metrics })}</Badge>
        <Badge>{t("doc.evidence", { count: counts.evidence })}</Badge>
        <Badge tone={counts.free_numbers ? "warning" : "success"}>{t("doc.freeNumbers", { count: counts.free_numbers })}</Badge>
      </div>
      <ul className="grid gap-1 text-body-sm">
        {diagnostics.map((d, i) => <li key={i} className={d.severity === "error" ? "text-destructive" : "text-muted-foreground"}>
          {severityLabel(d.severity)}: {diagnosticMessage(d)}</li>)}
      </ul>
    </div>
  );
}

function Suggestions({ id, dirty, decide }: { id: string; dirty: boolean; decide: ReturnType<typeof useDecide> }) {
  const { t } = useTranslation();
  const s = useSuggestions(id);
  const blocked = acceptBlockedReason(dirty);
  const list = s.data ?? [];
  if (!list.length) return <p className="text-body-sm text-muted-foreground">{t("doc.noSuggestions")}</p>;
  return (
    <ul className="grid gap-3">
      {list.map((x) => (
        <li key={x.id} className="grid gap-1 rounded-md border p-2 text-body-sm">
          <span className="text-caption text-muted-foreground">{x.op} · {x.rationale || x.author}</span>
          <pre className="whitespace-pre-wrap font-sans">{x.text}</pre>
          <div className="flex gap-2">
            {[true, false].map((accept) => (
              <Button key={String(accept)} size="sm" variant={accept ? "default" : "ghost"}
                disabled={decide.isPending || (accept && blocked !== null)} title={accept ? blocked ?? undefined : undefined}
                onClick={() => decide.mutate({ sid: x.id, accept, key: newKey() }, {
                  onSuccess: (r) => { const k = DECIDED[r.status]; toast.message(k ? t(k) : t("doc.suggestionDecided", { status: r.status })); }, onError: (e) => toast.error(describeError(e)) })}>
                {accept ? t("doc.accept") : t("doc.reject")}</Button>))}
          </div>
        </li>))}
    </ul>
  );
}

function Editor({ doc }: { doc: WDoc }) {
  const { t } = useTranslation();
  const [title, setTitle] = useState(doc.title);
  const [body, setBody] = useState(doc.body);
  const save = useSaveDoc(doc.id);
  const decide = useDecide(doc.id);
  const exp = useExport(doc.id);
  const [language, setLanguage] = useState<ExportLanguage>("en");
  const dirty = title !== doc.title || body !== doc.body;
  const run = (exporter: string, mode: "draft" | "final") => exp.mutate({ exporter, mode, language, key: newKey() }, {
    onSuccess: (r) => toast.success(t("doc.exportDone", { mode: mode === "final" ? t("doc.modeFinal") : t("doc.modeDraft"), status: r.status,
      files: r.files.map((f) => f.path.split("/").pop()).join(", ") })),
    onError: (e) => toast.error(describeError(e)),
  });
  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
      <div className="grid gap-2">
        <Input disabled={decide.isPending} value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t("doc.titlePlaceholder")} />
        <Textarea disabled={decide.isPending} className="min-h-[60vh] font-mono text-body-sm" value={body} onChange={(e) => setBody(e.target.value)} />
        <div className="flex flex-wrap gap-2">
          <Button disabled={!dirty || decide.isPending} loading={save.isPending} onClick={() => save.mutate({ title, body, base_version: doc.version, key: newKey() },
            { onError: (e) => toast.error(describeError(e)) })}>{t("common.save")}</Button>
          {EXPORTERS.map(([name, label]) => (
            <span key={name} className="flex gap-1">
              <Button variant="outline" size="sm" disabled={dirty || decide.isPending || exp.isPending} onClick={() => run(name, "final")}>{t(label)}</Button>
              <Button variant="ghost" size="sm" disabled={dirty || decide.isPending || exp.isPending} onClick={() => run(name, "draft")}>{t("doc.draftButton", { label: t(label) })}</Button>
            </span>))}
          <ExportLanguageSelect id="doc-export-language" value={language} onChange={setLanguage} />
        </div>
      </div>
      <div className="grid content-start gap-4">
        {exp.error && <p role="alert">{describeError(exp.error)}</p>}
        {exp.data && <ExportResult label={`${exp.variables?.exporter} · ${exp.variables?.mode}`} result={exp.data} />}

        <Card><CardHeader><CardTitle>{t("doc.checkTitle")}</CardTitle></CardHeader>
          <CardContent><GatePanel id={doc.id} version={doc.version} /></CardContent></Card>
        <Card><CardHeader><CardTitle>{t("doc.suggestionsTitle")}</CardTitle></CardHeader><CardContent><Suggestions id={doc.id} dirty={dirty} decide={decide} /></CardContent></Card>
      </div>
    </div>
  );
}

export function DocEditorPage() {
  const { t } = useTranslation();
  const { id = "", docId = "" } = useParams();
  const q = useDoc(docId);
  if (q.isPending) return <ListSkeleton rows={4} />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  return (
    <>
      <Link to={`/projects/${id}`} className="mb-3 inline-flex items-center gap-1 text-body-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" />{t("doc.back")}</Link>
      <PageHeader title={q.data.title || t("common.untitled")} description={t("doc.meta", { kind: codeLabel("docKinds", q.data.kind), version: q.data.version })} />
      <Editor key={q.data.version} doc={q.data} />  {/* new revision (save / accepted suggestion) -> fresh state */}
    </>
  );
}
