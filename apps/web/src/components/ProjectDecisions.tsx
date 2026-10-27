import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { QueryView } from "@/components/layout/state";
import { humanize, relativeTime } from "@/lib/utils";

export interface Decision {
  id: string; kind: string; question: string; choice: string; rationale: string; created_at: string;
  alternatives: { option: string; why_not: string }[] | null; support: "supported" | "owner_judgement";
  links: { ref: string; live: boolean }[]; broken_links: string[];
}

/** Decision records of a project (ADR-184): why each research choice was made and what it rests on. Read-only:
 *  records are written by the owner's tools and revised only by superseding. */
export function ProjectDecisions({ pid }: { pid: string }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["decisions", pid],
    queryFn: ({ signal }) => api.get<Decision[]>(`/projects/${encodeURIComponent(pid)}/decisions`, signal) });
  return <Card>
    <CardHeader><CardTitle>{t("decisions.title")}</CardTitle><CardDescription>{t("decisions.description")}</CardDescription></CardHeader>
    <CardContent><QueryView q={q}>{list => list.length === 0 ? <p className="text-body-sm text-muted-foreground">{t("decisions.none")}</p> :
      <ul className="grid gap-3">{list.map(d => <li key={d.id} className="grid gap-1 rounded-md border p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="neutral">{humanize(d.kind)}</Badge>
          <Badge tone={d.support === "supported" ? "success" : "warning"}>{d.support === "supported" ? t("decisions.supported") : t("decisions.owner")}</Badge>
          {d.broken_links.length > 0 && <Badge tone="danger">{t("decisions.brokenLinks", { count: d.broken_links.length })}</Badge>}
          <span className="text-caption text-muted-foreground">{relativeTime(d.created_at)}</span>
        </div>
        <p className="font-medium">{d.question}</p><p>{d.choice}</p>
        <p className="text-body-sm text-muted-foreground">{d.rationale}</p>
        {(d.alternatives ?? []).length > 0 && <ul className="text-body-sm">{(d.alternatives ?? []).map(a => <li key={a.option}>{t("decisions.notChosen", { option: a.option, why: a.why_not })}</li>)}</ul>}
        {d.broken_links.length > 0 && <p className="text-body-sm text-destructive">{t("decisions.broken", { list: d.broken_links.join(", ") })}</p>}
      </li>)}</ul>}</QueryView></CardContent>
  </Card>;
}
