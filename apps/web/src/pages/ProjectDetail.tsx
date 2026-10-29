import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { ArrowLeft, FileText, Pencil, Plus } from "lucide-react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { codeLabel } from "@/i18n/taskStages";
import { describeError } from "@/api/client";
import { useEvents, useNotes, useProject, useTasks, useUpdateProject } from "@/api/queries";
import type { Note, Project } from "@/api/types";
import { listedDocs, useCreateDoc, useDocs } from "@/api/writing";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Textarea } from "@/components/ui/input";
import { PageHeader } from "@/components/layout/AppShell";
import { EmptyState, ErrorState, ListSkeleton, QueryView } from "@/components/layout/state";
import { ProjectDecisions } from "@/components/ProjectDecisions";
import { ResearchPlanning } from "@/components/ResearchPlanning";
import { StatusBadge } from "@/components/StatusBadge";
import { useNewShortcut } from "@/lib/shortcuts";
import { newKey, relativeTime } from "@/lib/utils";
import { NoteEditor } from "./NoteEditor";

function NextStep({ p }: { p: Project }) {
  const { t } = useTranslation();
  const upd = useUpdateProject(p.id);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(p.next_step);
  const [intentId, setIntentId] = useState(newKey);
  const save = () => upd.mutate({ revision: p.revision, next_step: text, intentId }, {
    onSuccess: () => { setEditing(false); setIntentId(newKey()); toast.success(t("project.nextUpdated")); },
    onError: (e) => toast.error(describeError(e)),
  });
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div className="grid gap-1"><CardTitle>{t("project.nextTitle")}</CardTitle><CardDescription>{t("project.nextDescription")}</CardDescription></div>
        {!editing ? <Button variant="ghost" size="sm" onClick={() => setEditing(true)}><Pencil />{t("common.edit")}</Button> : null}
      </CardHeader>
      <CardContent>
        {editing ? (
          <div className="grid gap-2">
            <Field label={t("project.nextTitle")} htmlFor="ns"><Textarea id="ns" className="min-h-20" value={text} onChange={(e) => setText(e.target.value)} /></Field>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => { setEditing(false); setText(p.next_step); }}>{t("common.cancel")}</Button>
              <Button onClick={save} loading={upd.isPending}>{t("common.save")}</Button>
            </div>
          </div>
        ) : <p className="text-body">{p.next_step || <span className="text-muted-foreground">{t("project.notSet")}</span>}</p>}
      </CardContent>
    </Card>
  );
}

function Notes({ pid }: { pid: string }) {
  const { t } = useTranslation();
  const q = useNotes(pid);
  const [editing, setEditing] = useState<Note | "new" | null>(null);
  useNewShortcut(useCallback(() => setEditing("new"), []));
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div className="grid gap-1"><CardTitle>{t("project.notesTitle")}</CardTitle><CardDescription>{t("project.notesDescription")}</CardDescription></div>
        <Button variant="outline" size="sm" onClick={() => setEditing("new")}><Plus />{t("project.newNote")}</Button>
      </CardHeader>
      <CardContent>
        {editing ? <NoteEditor projectId={pid} note={editing === "new" ? null : editing} onDone={() => setEditing(null)} /> : (
          <QueryView q={q}>
            {(notes) => notes.length === 0 ? <EmptyState icon={<FileText />} title={t("project.noNotes")} body={t("project.noNotesBody")} /> : (
              <ul className="divide-y">
                {notes.map((n) => (
                  <li key={n.id}>
                    <button className="flex w-full items-start justify-between gap-3 py-3 text-left hover:text-primary" onClick={() => setEditing(n)}>
                      <span className="grid gap-0.5">
                        <span className="font-medium">{n.title || t("common.untitled")}{n.conflict_of ? t("project.conflictCopy") : ""}</span>
                        <span className="line-clamp-1 text-body-sm text-muted-foreground">{n.body}</span>
                      </span>
                      <span className="shrink-0 text-caption text-muted-foreground">{relativeTime(n.updated_at)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </QueryView>
        )}
      </CardContent>
    </Card>
  );
}

function Docs({ pid }: { pid: string }) {
  const { t } = useTranslation();
  const q = useDocs(pid);
  const create = useCreateDoc(pid);
  const nav = useNavigate();
  const make = (kind: "report" | "manuscript") => create.mutate({ kind, title: kind === "report" ? t("project.newReportTitle") : t("project.newManuscriptTitle"), key: newKey() }, {
    onSuccess: (d) => void nav(`/projects/${pid}/docs/${d.id}`), onError: (e) => toast.error(describeError(e)) });
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div className="grid gap-1"><CardTitle>{t("project.docsTitle")}</CardTitle>
          <CardDescription>{t("project.docsDescription")}</CardDescription></div>
        <div className="flex gap-1">
          <Button variant="outline" size="sm" onClick={() => make("manuscript")}><Plus />{t("project.manuscript")}</Button>
          <Button variant="ghost" size="sm" onClick={() => make("report")}><Plus />{t("project.report")}</Button>
        </div>
      </CardHeader>
      <CardContent>
        <QueryView q={q}>
          {(all) => { const docs = listedDocs(all); return docs.length === 0 ? <p className="text-body-sm text-muted-foreground">{t("project.noneYet")}</p> : (
            <ul className="divide-y">{docs.map((d) => (
              <li key={d.id}><Link className="flex justify-between gap-3 py-2 hover:text-primary" to={`/projects/${pid}/docs/${d.id}`}>
                <span>{d.title || t("common.untitled")} <span className="text-caption text-muted-foreground">({codeLabel("docKinds", d.kind)})</span></span>
                <span className="text-caption text-muted-foreground">{relativeTime(d.updated_at)}</span></Link></li>))}
            </ul>); }}
        </QueryView>
      </CardContent>
    </Card>
  );
}

function Activity({ pid }: { pid: string }) {
  const { t } = useTranslation();
  const ev = useEvents(pid);
  const tasks = useTasks(pid);
  return (
    <Card>
      <CardHeader><CardTitle>{t("project.activityTitle")}</CardTitle><CardDescription>{t("project.activityDescription")}</CardDescription></CardHeader>
      <CardContent className="grid gap-3">
        {(tasks.data ?? []).slice(0, 3).map((t) => (
          <div key={t.id} className="flex items-center justify-between text-body-sm"><span className="font-mono">{t.kind}</span><StatusBadge state={t.state} /></div>
        ))}
        <QueryView q={ev}>
          {(list) => list.length === 0 ? <p className="text-body-sm text-muted-foreground">{t("project.noEvents")}</p> : (
            <ul className="grid gap-1.5">
              {list.slice(-8).reverse().map((e) => (
                <li key={e.seq} className="flex justify-between gap-2 text-body-sm">
                  <span className="font-mono text-caption">{e.kind}</span>
                  <span className="text-caption text-muted-foreground">{relativeTime(e.ts)}</span>
                </li>
              ))}
            </ul>
          )}
        </QueryView>
      </CardContent>
    </Card>
  );
}

export function ProjectDetailPage() {
  const { t } = useTranslation();
  const { id = "" } = useParams();
  const q = useProject(id);
  if (q.isPending) return <ListSkeleton rows={3} />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />;
  const p = q.data;
  return (
    <>
      <Link to="/projects" className="mb-3 inline-flex items-center gap-1 text-body-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" />{t("project.back")}
      </Link>
      <PageHeader title={p.title} description={p.goal || t("projects.noGoal")} />
      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div className="grid gap-4"><NextStep p={p} /><Docs pid={p.id} /><ResearchPlanning key={p.id} pid={p.id} /><ProjectDecisions pid={p.id} /><Notes pid={p.id} /></div>
        <Activity pid={p.id} />
      </div>
    </>
  );
}
