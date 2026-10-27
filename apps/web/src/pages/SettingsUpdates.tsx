// Settings > About / Updates (ADR-223): current version, explicit check, one-click update with progress, mirror prefix.
// Nothing here talks to the network until the owner clicks; the host does the download and the sha256 check.
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import { ApiError, describeError } from "@/api/client";
import { qk, updateCalls, useUpdateStatus } from "@/api/queries";
import type { UpdateState, UpdateStatus } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/input";
import { QueryView } from "@/components/layout/state";
import { newKey } from "@/lib/utils";

const ERROR_KEYS: Record<string, ParseKeys> = {
  update_network: "update.errors.update_network",
  update_timeout: "update.errors.update_timeout",
  update_http_error: "update.errors.update_http_error",
  update_bad_metadata: "update.errors.update_bad_metadata",
  update_checksum_mismatch: "update.errors.update_checksum_mismatch",
  update_bad_mirror: "update.errors.update_bad_mirror",
  update_not_available: "update.errors.update_not_available",
  update_busy: "update.errors.update_busy",
  update_not_ready: "update.errors.update_not_ready",
  update_unsupported: "update.errors.update_unsupported",
  update_launch_failed: "update.errors.update_launch_failed",
};

const STATE_KEYS: Record<UpdateState, ParseKeys> = {
  idle: "update.stateIdle", checking: "update.stateChecking", up_to_date: "update.stateUpToDate",
  available: "update.stateAvailable", downloading: "update.stateDownloading", ready: "update.stateReady",
  installing: "update.stateInstalling", error: "update.stateError",
};

/** Update error in the UI language by its stable code; an unknown code shows the server text as sent. */
export function useUpdateErrorText() {
  const { t } = useTranslation();
  return (code: string, message: string) => (ERROR_KEYS[code] ? t(ERROR_KEYS[code]) : message);
}

const mb = (bytes: number) => (bytes / 1048576).toFixed(1);

function Progress({ received, total }: { received: number; total: number }) {
  const { t } = useTranslation();
  const pct = total > 0 ? Math.min(100, Math.round((received / total) * 100)) : null;
  return (
    <div className="grid gap-1">
      <div className="h-2 overflow-hidden rounded bg-muted" role="progressbar" aria-label={t("update.progressAria")}
        aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct ?? undefined}>
        <div className="h-full bg-primary transition-all" style={{ width: `${pct ?? 0}%` }} />
      </div>
      <p className="text-caption text-muted-foreground">
        {total > 0 ? t("update.progress", { received: mb(received), total: mb(total), pct }) : t("update.progressUnknown", { received: mb(received) })}
      </p>
    </div>
  );
}

function MirrorField({ current }: { current: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const errorText = useUpdateErrorText();
  const [val, setVal] = useState(current);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const save = async () => {
    setBusy(true); setErr(null);
    try {
      qc.setQueryData(qk.update, await updateCalls.setMirror(val, newKey()));
      toast.success(t("update.mirrorSaved"));
    } catch (e) {
      setErr(e instanceof ApiError ? errorText(e.code, e.message) : describeError(e));
    } finally { setBusy(false); }
  };
  return (
    <Field label={t("update.mirrorLabel")} htmlFor="update-mirror" hint={t("update.mirrorHint")} error={err}>
      <div className="flex gap-2">
        <Input id="update-mirror" value={val} placeholder={t("update.mirrorPlaceholder")} autoComplete="off" spellCheck={false}
          onChange={(e) => setVal(e.target.value)} />
        <Button variant="outline" loading={busy} disabled={val.trim() === current} onClick={() => void save()}>{t("common.save")}</Button>
      </div>
    </Field>
  );
}

const OFFERING: readonly UpdateState[] = ["available", "downloading", "ready", "installing"];

/** A newer release is known (also after a failed download; not after a failed check). */
function offersUpdate(s: UpdateStatus): boolean {
  if (!s.latest) return false;
  if (s.state === "error") return s.error?.code !== "update_network" && s.error?.code !== "update_timeout" && s.error?.code !== "update_http_error";
  return OFFERING.includes(s.state);
}

const TONES: Partial<Record<UpdateState, "danger" | "success" | "neutral">> = { error: "danger", up_to_date: "success", idle: "neutral" };

function Versions({ s }: { s: UpdateStatus }) {
  const { t } = useTranslation();
  const row = (label: string, value: React.ReactNode) => (
    <div className="flex items-center justify-between gap-3"><dt className="text-muted-foreground">{label}</dt><dd>{value}</dd></div>);
  return (
    <dl className="grid gap-2 text-body-sm">
      {row(t("update.current"), <span className="font-mono">{s.current_version}</span>)}
      {s.latest ? row(t("update.latest"), <span className="font-mono">{s.latest.version}</span>) : null}
      {row(t("update.status"), <Badge tone={TONES[s.state] ?? "primary"}>{t(STATE_KEYS[s.state])}</Badge>)}
    </dl>
  );
}

function Messages({ s }: { s: UpdateStatus }) {
  const { t } = useTranslation();
  const errorText = useUpdateErrorText();
  return (
    <>
      {s.error ? <p className="text-body-sm text-destructive" role="alert">{errorText(s.error.code, s.error.message)}</p> : null}
      {s.progress && s.state !== "ready" ? <Progress {...s.progress} /> : null}
      {s.state === "installing" ? <p className="text-body-sm">{t("update.installingHint")}</p> : null}
      {s.state === "ready" && !s.can_install ? (
        <p className="text-body-sm">{t("update.readyManual")} <span className="break-all font-mono text-caption">{s.installer}</span></p>
      ) : null}
      {offersUpdate(s) && s.latest?.notes ? (
        <div className="grid gap-1"><p className="text-caption text-muted-foreground">{t("update.notes")}</p>
          <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 text-caption">{s.latest.notes}</pre></div>
      ) : null}
    </>
  );
}

function useUpdateActions(s: UpdateStatus) {
  const qc = useQueryClient();
  const errorText = useUpdateErrorText();
  const [busy, setBusy] = useState<"check" | "update" | null>(null);
  const installWhenReady = useRef(false);
  const fail = async (e: unknown) => {
    installWhenReady.current = false;
    toast.error(e instanceof ApiError ? errorText(e.code, e.message) : describeError(e));
    await qc.invalidateQueries({ queryKey: qk.update });
  };
  const install = () => updateCalls.install(newKey()).then((d) => qc.setQueryData(qk.update, d)).catch(fail);
  const run = async (what: "check" | "update", call: () => Promise<UpdateStatus>) => {
    setBusy(what);
    try { qc.setQueryData(qk.update, await call()); } catch (e) { await fail(e); } finally { setBusy(null); }
  };
  // one click: "Update" downloads, then installs as soon as the host has verified the installer
  useEffect(() => {
    if (!installWhenReady.current || (s.state !== "ready" && s.state !== "error")) return;
    installWhenReady.current = false;
    if (s.state === "ready" && s.can_install) void install();
  }, [s.state, s.can_install]);  // eslint-disable-line react-hooks/exhaustive-deps
  const update = () => {
    installWhenReady.current = s.can_install;
    if (s.state === "ready") { if (s.can_install) void install(); return; }
    void run("update", () => updateCalls.download(newKey()));
  };
  const check = () => void run("check", () => updateCalls.check(newKey()));
  return { busy, check, update };
}

function Actions({ s }: { s: UpdateStatus }) {
  const { t } = useTranslation();
  const { busy, check, update } = useUpdateActions(s);
  const running = s.state === "downloading" || s.state === "installing";
  const label = s.state === "ready" && s.can_install ? t("update.installNow") : t("update.updateNow", { version: s.latest?.version });
  return (
    <div className="flex flex-wrap gap-2">
      <Button variant="outline" loading={busy === "check" || s.state === "checking"} disabled={busy !== null || running}
        onClick={check}>{t("update.check")}</Button>
      {offersUpdate(s) ? (
        <Button loading={busy === "update" || s.state === "downloading"}
          disabled={busy !== null || s.state === "installing" || (s.state === "ready" && !s.can_install)} onClick={update}>{label}</Button>
      ) : null}
    </div>
  );
}

export function UpdatesCard() {
  const { t } = useTranslation();
  const q = useUpdateStatus();
  return (
    <Card>
      <CardHeader><CardTitle>{t("update.title")}</CardTitle><CardDescription>{t("update.description")}</CardDescription></CardHeader>
      <CardContent><QueryView q={q}>{(s) => (
        <div className="grid gap-4">
          <Versions s={s} /><Messages s={s} /><Actions s={s} />
          <p className="text-caption text-muted-foreground">{t("update.noAutoCheck")}</p>
          <MirrorField key={s.mirror_prefix} current={s.mirror_prefix} />
        </div>
      )}</QueryView></CardContent>
    </Card>
  );
}
