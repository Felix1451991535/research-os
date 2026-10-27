// Note editing with offline drafts: every keystroke is saved locally; "Save" syncs with base_version.
// A version conflict never overwrites — both texts are shown and kept on the server.
import { useEffect, useState } from "react";
import { AlertTriangle, CloudOff, Save } from "lucide-react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { ApiError, OfflineError, describeError } from "@/api/client";
import { useCreateNote, useUpdateNote } from "@/api/notes";
import type { Note } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { dropDraft, loadDraft, saveDraft, type Draft } from "@/offline/drafts";
import { formatTime, newKey } from "@/lib/utils";

interface Conflict { server: Note; client_body: string }

function ConflictPanel({ conflict, onCombine }: { conflict: Conflict; onCombine: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-2 rounded-md border border-warning/40 bg-warning/10 p-3 text-body-sm" role="alert">
      <div className="flex items-center gap-2 font-medium text-warning"><AlertTriangle className="size-4" />
        {t("note.conflict")}</div>
      <div className="grid gap-2 md:grid-cols-2">
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-card p-2">{conflict.server.body}</pre>
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-card p-2">{conflict.client_body}</pre>
      </div>
      <Button variant="outline" size="sm" className="justify-self-start" onClick={onCombine}>{t("note.combine")}</Button>
    </div>
  );
}

/** Local draft state: loads a saved draft, autosaves every change to IndexedDB (400 ms debounce). */
function useNoteDraft(projectId: string, note: Note | null) {
  const [title, setTitle] = useState(note?.title ?? "");
  const [body, setBody] = useState(note?.body ?? "");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [fallbackOp] = useState(newKey); // stable per editing session, so retries reuse the same operation id
  const noteId = note?.id ?? null;
  useEffect(() => {
    void loadDraft(projectId, noteId).then((d) => {
      if (d) { setDraft(d); setTitle(d.title); setBody(d.body); }
    });
  }, [projectId, noteId]);
  const opId = draft?.client_operation_id ?? fallbackOp;
  useEffect(() => {
    const t = window.setTimeout(() => {
      if (title === (note?.title ?? "") && body === (note?.body ?? "")) return;
      void saveDraft({ project_id: projectId, note_id: noteId, base_version: note?.version ?? null, title, body,
        client_operation_id: opId }).then(setDraft);
    }, 400);
    return () => window.clearTimeout(t);
  }, [title, body, projectId, noteId, note, opId]);
  return { title, setTitle, body, setBody, draft, setDraft, opId, noteId };
}

export function NoteEditor({ projectId, note, onDone }: { projectId: string; note: Note | null; onDone: () => void }) {
  const { t } = useTranslation();
  const { title, setTitle, body, setBody, draft, setDraft, opId, noteId } = useNoteDraft(projectId, note);
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const create = useCreateNote(projectId);
  const update = useUpdateNote(projectId);

  const onError = (e: unknown) => {
    if (e instanceof ApiError && e.code === "version_conflict") {
      setConflict(e.body.details as unknown as Conflict);
    } else if (e instanceof OfflineError) {
      toast.warning(t("note.offlineSaved"), { icon: <CloudOff className="size-4" /> });
    } else toast.error(describeError(e));
  };
  const done = async () => { await dropDraft(projectId, noteId); toast.success(t("note.saved")); onDone(); };
  const save = () => {
    if (note) {
      update.mutate({ id: note.id, title, body, base_version: draft?.base_version ?? note.version, client_operation_id: opId },
        { onSuccess: done, onError });
    } else create.mutate({ title, body, client_operation_id: opId }, { onSuccess: done, onError });
  };

  return (
    <div className="grid gap-3">
      <Input aria-label={t("note.titleAria")} placeholder={t("note.titlePlaceholder")} value={title} onChange={(e) => setTitle(e.target.value)} />
      <Textarea aria-label={t("note.textAria")} className="min-h-56" placeholder={t("note.textPlaceholder")}
        value={body} onChange={(e) => setBody(e.target.value)} />
      {conflict ? <ConflictPanel conflict={conflict} onCombine={() => {
        setBody(`${conflict.server.body}\n\n${conflict.client_body}`);
        setDraft((d) => d && { ...d, base_version: conflict.server.version });
        setConflict(null);
      }} /> : null}
      <div className="flex items-center justify-between gap-2">
        <span className="text-caption text-muted-foreground">{draft ? t("note.draftSaved", { time: formatTime(draft.updated_at) }) : ""}</span>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={onDone}>{t("common.close")}</Button>
          <Button onClick={save} loading={create.isPending || update.isPending}><Save />{t("common.save")}</Button>
        </div>
      </div>
    </div>
  );
}
