import { PairedRuns } from "@/components/PairedRuns";
import { RunComparison } from "@/components/RunComparison";
import { stateLabel } from "@/components/StatusBadge";
import { useState } from "react";
import { useParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useExperimentRun, useExperimentTask, useRunAttempts } from "@/api/queries";
import { PageHeader } from "@/components/layout/AppShell";
import { EmptyState, QueryView } from "@/components/layout/state";
import { JsonDetails } from "@/components/ExecutionDetails";
import { ExportLanguageSelect, ExportResult, type ExportLanguage } from "@/components/ExportResult";
import { Button } from "@/components/ui/button";
import { useRunExport } from "@/api/writing";
import { describeError } from "@/api/client";
import { formatNumber, newKey } from "@/lib/utils";

export function ExperimentRunPage() {
  const { t } = useTranslation();
  const { taskId = "" } = useParams(); const task = useExperimentTask(taskId); const attempts = useRunAttempts(taskId);
  return <div className="grid gap-6 [&_button]:min-h-11"><PageHeader title={t("run.title")} />
    <QueryView q={task}>{tk => <section><h2>{tk.id} · {stateLabel(t, tk.state)}</h2>{tk.error_message && <p role="alert">{tk.error_message}</p>}</section>}</QueryView>
    <QueryView q={attempts}>{items => items.length ? items.map(r => <RunDetails key={r.id} id={r.id} />) : <EmptyState title={t("run.noAttempts")} body={t("run.noAttemptsBody")} />}</QueryView>
  </div>;
}
function RunDetails({ id }: { id: string }) {
  const { t } = useTranslation();
  const run = useExperimentRun(id);
  return <QueryView q={run}>{r => <section className="grid gap-3 rounded-md border p-3">
    <h2 className="text-title">{t("run.heading", { id: r.id, attempt: r.attempt, state: stateLabel(t, r.state) })}</h2>
    {r.error_message && <p role="alert">{r.error_message}</p>}
    <h3>{t("run.metrics")}</h3>{r.metrics.length ? <div className="overflow-auto"><table className="w-full text-left"><thead><tr><th>{t("run.metric")}</th><th>{t("run.split")}</th><th>{t("run.value")}</th><th>{t("run.verification")}</th></tr></thead>
      <tbody>{r.metrics.map((m, i) => <tr key={i}><td>{m.name}</td><td>{m.split}</td><td className="tabular-nums" title={m.value === null || m.value === undefined ? undefined : String(m.value)}>
        {m.value === null || m.value === undefined ? t("common.unavailable") : formatNumber(m.value)}</td><td>{m.verification}</td></tr>)}</tbody></table></div> : <EmptyState title={t("run.noMetrics")} />}
    <RunFigures runId={r.id} artifacts={r.artifacts ?? []} />
    <RunComparison run={r} />
    {r.task_state === "succeeded" && <PairedRuns runId={r.id} />}
    <RunSlides runId={r.id} />
    <h3>{t("run.detectors")}</h3>{r.signals.length ? <ul className="grid gap-3">{r.signals.map(s => <li key={s.id}>
      <p>{s.detector} · {s.severity} · {s.rule}</p><JsonDetails value={{ value: s.value, selector: s.selector }} />
    </li>)}</ul> : <EmptyState title={t("run.noDetectors")} body={t("run.noDetectorsBody")} />}
  </section>}</QueryView>;
}

function RunSlides({ runId }: { runId: string }) {
  const { t } = useTranslation();
  const exp = useRunExport(runId);
  const [language, setLanguage] = useState<ExportLanguage>("en");
  return <div className="grid gap-2">
    <div className="flex flex-wrap items-center gap-3">
      <Button variant="outline" loading={exp.isPending}
        onClick={() => exp.mutate({ exporter: "report_slides", language, key: newKey() })}>{t("run.exportSlides")}</Button>
      <ExportLanguageSelect id="run-export-language" value={language} onChange={setLanguage} />
    </div>
    {exp.error && <p role="alert">{describeError(exp.error)}</p>}
    {exp.data && <ExportResult label={`report_slides · ${t("doc.modeFinal")}`} result={exp.data} />}
  </div>;
}

/** Figures of the run (host- or template-drawn image artifacts, ADR-194/209), each served after a hash check. */
function RunFigures({ runId, artifacts }: { runId: string; artifacts: { path: string; kind: string }[] }) {
  const { t } = useTranslation();
  const names = artifacts.filter(a => a.kind === "figure").map(a => a.path.split("/").pop() ?? "").filter(Boolean);
  if (!names.length) return null;
  return <section className="grid gap-2"><h3>{t("run.figures")}</h3>
    <div className="grid gap-3 sm:grid-cols-2">{names.map(n => <figure key={n} className="grid gap-1 rounded-md border bg-white p-2">
      <img src={`/api/v1/runs/${encodeURIComponent(runId)}/figures/${encodeURIComponent(n)}`} alt={n} loading="lazy" className="max-w-full" />
      <figcaption className="text-caption text-muted-foreground">{n}</figcaption></figure>)}</div>
  </section>;
}
