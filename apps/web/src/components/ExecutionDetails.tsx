import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import i18n from "@/i18n";
import { ApiError, describeError } from "@/api/client";
import { authCalls } from "@/api/queries";
import { Field, Input } from "@/components/ui/input";
import { cn, formatNumber, humanize, newKey } from "@/lib/utils";
import { Link } from "react-router";
import type { Task } from "@/api/types";
import { EmptyState } from "@/components/layout/state";
import { stateLabel } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";

const LIST_PREVIEW = 8;

function Scalar({ value }: { value: unknown }) {
  const { t } = useTranslation();
  if (value === null || value === undefined) return <span className="text-muted-foreground">—</span>;
  if (typeof value === "number") return <span className="tabular-nums">{formatNumber(value)}</span>;
  if (typeof value === "boolean") return <span>{value ? t("common.yes") : t("common.no")}</span>;
  return <span className="break-words">{String(value)}</span>;
}

function Value({ value, depth }: { value: unknown; depth: number }) {
  const { t } = useTranslation();
  const [all, setAll] = useState(false);
  if (Array.isArray(value)) {
    if (!value.length) return <span className="text-muted-foreground">{t("common.none")}</span>;
    const shown = all ? value : value.slice(0, LIST_PREVIEW);
    const simple = value.every(v => v === null || typeof v !== "object");
    return <div className="grid gap-1">
      {simple ? <span className="break-words">{shown.map((v, i) => <span key={i}>{i > 0 && ", "}<Scalar value={v} /></span>)}</span>
        : <ol className="grid gap-2">{shown.map((v, i) => <li key={i} className="rounded-md border p-2"><Value value={v} depth={depth + 1} /></li>)}</ol>}
      {value.length > LIST_PREVIEW && <Button variant="link" className="justify-self-start" onClick={() => setAll(!all)}>
        {all ? t("details.showFewer") : t("details.showAll", { count: value.length })}</Button>}
    </div>;
  }
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    if (!entries.length) return <span className="text-muted-foreground">{t("common.none")}</span>;
    return <dl className={depth ? "grid gap-1 border-l pl-3" : "grid gap-2"}>{entries.map(([k, v]) => <div key={k} className={cn("grid gap-x-3", depth ? "sm:grid-cols-[minmax(5rem,10rem)_minmax(0,1fr)]" : "sm:grid-cols-[minmax(6rem,12rem)_minmax(0,1fr)]")}>
      <dt className="text-body-sm font-medium text-muted-foreground">{humanize(k)}</dt>
      <dd className="min-w-0 wrap-anywhere"><Value value={v} depth={depth + 1} /></dd></div>)}</dl>;
  }
  return <Scalar value={value} />;
}

/** Structured, readable view of API data (labels, formatted numbers, long lists folded); raw JSON on demand. */
export function JsonDetails({ value }: { value: unknown }) {
  const { t } = useTranslation();
  return <div className="grid gap-2 rounded-md border p-3 text-body-sm">
    <Value value={value} depth={0} />
    <details><summary className="cursor-pointer text-muted-foreground">{t("details.rawJson")}</summary>
      <pre className="overflow-auto whitespace-pre-wrap break-words">{JSON.stringify(value, null, 2)}</pre></details>
  </div>;
}

export function parseObject(text: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(i18n.t("details.jsonObject"));
  return value as Record<string, unknown>;
}

const TASK_PREVIEW = 20;

export function ExperimentTasks({ tasks }: { tasks: Task[] }) {
  const { t } = useTranslation();
  const [all, setAll] = useState(false);
  if (!tasks.length) return <EmptyState title={t("details.noRuns")} />;
  const shown = all ? tasks : tasks.slice(0, TASK_PREVIEW);
  return <div className="grid gap-2">
    <ul className="grid gap-2">{shown.map(task => <li key={task.id} className="grid gap-1 rounded-md border p-3 sm:grid-cols-[1fr_auto] sm:items-center">
      <Button asChild variant="link" size="touch" className="justify-self-start"><Link to={`/experiments/tasks/${encodeURIComponent(task.id)}`}>
        {String(task.payload.tag ?? task.payload.stage ?? t("details.run"))}{task.payload.seed !== undefined ? t("details.seed", { seed: String(task.payload.seed) }) : ""}</Link></Button>
      <span className="text-body-sm text-muted-foreground">{stateLabel(t, task.state)}</span>
      {task.error_message && <p className="sm:col-span-2">{task.error_message}</p>}
    </li>)}</ul>
    {tasks.length > TASK_PREVIEW && <Button variant="link" className="justify-self-start" onClick={() => setAll(!all)}>
      {all ? t("details.showFewer") : t("details.showAllRuns", { count: tasks.length })}</Button>}
  </div>;
}

/** The remote approval policy is owned by the host; ask for a password only when it requests reauth. */
export function ApprovalReauthentication({ error }: { error: unknown }) {
  if (!(error instanceof ApiError) || error.code !== "reauth_required") return null;
  return <Reauthenticate />;
}
function Reauthenticate() {
  const { t } = useTranslation();
  const [password, setPassword] = useState("");
  const reauth = useMutation({ mutationFn: () => authCalls.reauth(password, newKey()),
    onSuccess: () => setPassword("") });
  if (reauth.isSuccess) return <p role="status">{t("details.identityConfirmed")}</p>;
  return <div className="grid gap-2">
    <Field label={t("details.confirmPassword")} htmlFor="approval-password"><Input id="approval-password" className="h-11" type="password" autoComplete="current-password"
      value={password} disabled={reauth.isPending} onChange={e => setPassword(e.target.value)} /></Field>
    <Button size="touch" disabled={!password || reauth.isPending} onClick={() => reauth.mutate()}>{t("details.verify")}</Button>
    {reauth.error && <p role="alert">{describeError(reauth.error)}</p>}
  </div>;
}
