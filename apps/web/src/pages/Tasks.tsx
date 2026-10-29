// Task queue: real backend state only (no invented percentages). Cancel asks for confirmation.
import { useState } from "react";
import { CheckCircle2, ListChecks, Play, RotateCw, Square } from "lucide-react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import i18n from "@/i18n";
import { stageLabel } from "@/i18n/taskStages";
import { describeError } from "@/api/client";
import { useApprove, useCreateTask, useHost, useTaskAction, useTasks } from "@/api/queries";
import type { Task } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/dialog";
import { PageHeader } from "@/components/layout/AppShell";
import { EmptyState, QueryView } from "@/components/layout/state";
import { StatusBadge } from "@/components/StatusBadge";
import { newKey, relativeTime } from "@/lib/utils";

function stageText(t: Task): string {
  if (t.state === "running" || t.state === "cancelling") {
    const p = t.progress as { elapsed_s?: number; total_s?: number };
    const stage = t.stage ? stageLabel(t.stage) : null;
    if (stage && p.elapsed_s !== undefined) return i18n.t("tasks.elapsed", { stage, seconds: p.elapsed_s });
    return stage ?? i18n.t("tasks.started");
  }
  if (t.error_code) return `${t.error_code}${t.error_message ? `: ${t.error_message}` : ""}`;
  return "";
}

function TaskRow({ t: task }: { t: Task }) {
  const { t } = useTranslation();
  const act = useTaskAction();
  const approve = useApprove();
  const [confirm, setConfirm] = useState(false);
  const run = (action: "cancel" | "retry") =>
    act.mutate({ id: task.id, action, intentId: `${task.id}:${action}:${task.updated_at}` }, {
      onSuccess: () => { setConfirm(false); toast.success(action === "cancel" ? t("tasks.stopRequested") : t("tasks.queuedAgain")); },
      onError: (e) => toast.error(describeError(e)),
    });
  const live = ["queued", "running", "awaiting_approval", "draft"].includes(task.state);
  return (
    <Card className="flex flex-wrap items-center gap-x-4 gap-y-2 p-4">
      <div className="grid min-w-0 flex-1 gap-0.5">
        <div className="flex items-center gap-2"><span className="truncate font-mono text-body-sm">{task.kind}</span><StatusBadge state={task.state} /></div>
        <div className="truncate text-caption text-muted-foreground">
          {stageText(task) || t("tasks.attempt", { attempt: task.attempt, max: task.max_attempts })} · {t("tasks.created", { when: relativeTime(task.created_at) })} · {task.id}
        </div>
      </div>
      <div className="flex gap-2">
        {task.state === "awaiting_approval" ? (
          <Button size="sm" loading={approve.isPending} onClick={() => approve.mutate({ subjectType: "task", subjectId: task.id, intentId: newKey() },
            { onSuccess: () => toast.success(t("tasks.approved")), onError: (e) => toast.error(describeError(e)) })}>
            <CheckCircle2 />{t("tasks.approve")}</Button>
        ) : null}
        {["interrupted", "blocked"].includes(task.state) ? <Button size="sm" variant="outline" onClick={() => run("retry")}><RotateCw />{t("tasks.retry")}</Button> : null}
        {live ? <Button size="sm" variant="outline" onClick={() => setConfirm(true)}><Square />{t("tasks.stop")}</Button> : null}
      </div>
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} title={t("tasks.stopTitle")} destructive confirmLabel={t("tasks.stopConfirm")}
        description={task.state === "running" ? t("tasks.stopRunning") : t("tasks.stopQueued")}
        busy={act.isPending} onConfirm={() => run("cancel")} />
    </Card>
  );
}

export function TasksPage() {
  const { t } = useTranslation();
  const q = useTasks();
  const host = useHost();
  const create = useCreateTask();
  const selfTest = () => create.mutate({ kind: "system.sleep", payload: { seconds: 5 }, intentId: newKey() }, {
    onSuccess: () => toast.success(t("tasks.selfTestQueued")),
    onError: (e) => toast.error(describeError(e)),
  });
  return (
    <>
      <PageHeader title={t("tasks.title")} description={t("tasks.description")}
        actions={<Button variant="outline" onClick={selfTest} loading={create.isPending}><Play />{t("tasks.selfTest")}</Button>} />
      {host.data && !(host.data.worker as { alive?: boolean } | undefined)?.alive ? (
        <div className="mb-3 rounded-md bg-warning/15 px-3 py-2 text-body-sm text-warning" role="status">
          {t("tasks.workerDown")}
        </div>) : null}
      <QueryView q={q}>
        {(list) => list.length === 0 ? (
          <EmptyState icon={<ListChecks />} title={t("tasks.emptyTitle")} body={t("tasks.emptyBody")} />
        ) : <div className="grid gap-2">{list.map((task) => <TaskRow key={task.id} t={task} />)}</div>}
      </QueryView>
    </>
  );
}
