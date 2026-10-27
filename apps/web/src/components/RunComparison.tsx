import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useCompareRun } from "@/api/queries";
import { describeError } from "@/api/client";
import type { CompareInput, ExperimentRun } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Field, Input, Textarea } from "@/components/ui/input";
import { JsonDetails } from "@/components/ExecutionDetails";

const mapping = () => ({ metric: "", split: "", evidence: "", index: "0", unit: "", kind: "absolute", tolerance: "0" });

export function RunComparison({ run }: { run: ExperimentRun }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [rows, setRows] = useState([mapping()]);
  const [params, setParams] = useState("[]");
  const [error, setError] = useState("");
  const compare = useCompareRun(run.id);
  const change = (i: number, key: keyof ReturnType<typeof mapping>, value: string) => {
    compare.reset(); setError(""); setRows(rows.map((row, n) => n === i ? { ...row, [key]: value } : row));
  };
  if (!open) return <Button variant="outline" onClick={() => setOpen(true)}>{t("compare.open", { id: run.id })}</Button>;
  return <section className="grid gap-3">
    <h3>{t("compare.title", { id: run.id })}</h3>
    <p>{t("compare.intro")}</p>
    <form className="grid gap-3" onSubmit={e => {
      e.preventDefault(); compare.reset(); setError("");
      try {
        const paperParams: unknown = JSON.parse(params);
        if (!Array.isArray(paperParams) || paperParams.length > 200 || paperParams.some(p => !p || typeof p !== "object" || Array.isArray(p))) throw new Error(t("compare.paramsArray"));
        const items = rows.map(row => {
          const index = Number(row.index), tolerance = Number(row.tolerance);
          if (!row.metric.trim() || !row.split.trim() || !row.evidence.trim() || !row.index.trim() || !Number.isSafeInteger(index) || index < 0 || !row.tolerance.trim() || !Number.isFinite(tolerance) || tolerance < 0) throw new Error(t("compare.rowInvalid"));
          return { metric: row.metric.trim(), split: row.split.trim(), evidence_id: row.evidence.trim(), number_index: index, unit: row.unit.trim() || null, tolerance: { kind: row.kind, value: tolerance } };
        });
        compare.mutate({ items, paper_params: paperParams } as CompareInput);
      } catch (err) { setError(describeError(err)); }
    }}>
      <fieldset disabled={compare.isPending} className="grid gap-3">
        {rows.map((row, i) => <fieldset key={i} className="grid gap-2 rounded-md border p-3">
          <legend>{t("compare.mapping", { n: i + 1 })}</legend>
          {([['metric', t("compare.metric")], ['split', t("compare.split")], ['evidence', t("compare.evidence")], ['index', t("compare.index")], ['unit', t("compare.unit")], ['tolerance', t("compare.tolerance")]] as const).map(([key, label]) => {
            const id = `${run.id}-${key === 'index' ? 'index' : key}-${i}`;
            return <Field key={key} label={label} htmlFor={id}><Input id={id} value={row[key]} required={key !== 'unit'}
              type={key === 'index' || key === 'tolerance' ? 'number' : 'text'} min={0} step={key === 'index' ? 1 : 'any'}
              list={key === 'metric' ? `${run.id}-metrics` : undefined} onChange={e => change(i, key, e.target.value)} /></Field>;
          })}
          <Field label={t("compare.toleranceKind")} htmlFor={`${run.id}-kind-${i}`}><select id={`${run.id}-kind-${i}`} value={row.kind} onChange={e => change(i, 'kind', e.target.value)}>
            <option value="absolute">{t("compare.absolute")}</option><option value="relative">{t("compare.relative")}</option>
          </select></Field>
          <Button type="button" variant="ghost" disabled={rows.length === 1} onClick={() => { compare.reset(); setRows(rows.filter((_, n) => n !== i)); }}>{t("compare.removeMapping", { n: i + 1 })}</Button>
        </fieldset>)}
        <datalist id={`${run.id}-metrics`}>{run.metrics.map((m, i) => <option key={i} value={m.name}>{m.split}</option>)}</datalist>
        <Button type="button" variant="outline" disabled={rows.length >= 200} onClick={() => { compare.reset(); setRows([...rows, mapping()]); }}>{t("compare.addMapping")}</Button>
        <Field label={t("compare.paramSpecs")} htmlFor={`${run.id}-params`} hint={t("compare.paramSpecsHint")}>
          <Textarea id={`${run.id}-params`} value={params} onChange={e => { compare.reset(); setError(""); setParams(e.target.value); }} />
        </Field>
        <Button type="submit" loading={compare.isPending}>{t("common.compare")}</Button>
      </fieldset>
    </form>
    {(error || compare.error) && <p role="alert">{error || describeError(compare.error)}</p>}
    {compare.data && <div className="grid gap-3" aria-live="polite">
      <p>{t("compare.resultFor", { run: compare.data.run_id, plan: compare.data.plan_id, revision: compare.data.revision })}</p>
      {compare.data.items.length === 0 ? <p>{t("compare.noItems")}</p> : <div className="overflow-auto"><table className="w-full text-left">
        <thead><tr><th>{t("compare.metricSplit")}</th><th>{t("compare.reported")}</th><th>{t("compare.reproduced")}</th><th>{t("compare.unit")}</th><th>{t("compare.tolerance")}</th><th>{t("compare.result")}</th></tr></thead>
        <tbody>{compare.data.items.map((item, i) => <tr key={i}>
          <td>{item.metric} / {item.split}</td><td>{item.reported_value ?? t("common.unavailable")}</td><td>{item.reproduced_value ?? t("common.unavailable")}</td><td>{item.canonical_unit ?? t("common.unavailable")}</td>
          <td>{item.tolerance.kind}: {item.tolerance.value}{item.threshold !== undefined && t("compare.threshold", { value: item.threshold })}</td>
          <td>{item.status === "match" ? t("compare.within") : item.status === "deviation" ? t("compare.outside") : t("compare.notComparable")}{item.reason && <p>{item.reason}</p>}</td>
        </tr>)}</tbody>
      </table></div>}
      {compare.data.items.map((item, i) => <div key={i} className="grid gap-2">
        <h4>{t("compare.itemHeading", { metric: item.metric, split: item.split })}</h4>
        {item.candidate_causes.map((cause, n) => <div key={n}><p>{t("compare.cause", { name: cause.name })}</p><JsonDetails value={cause} /></div>)}
        {item.parameter_notes?.map((note, n) => <p key={n}>{note}</p>)}
        <details><summary>{t("compare.evidenceDetails")}</summary><JsonDetails value={item} /></details>
      </div>)}
    </div>}
  </section>;
}
