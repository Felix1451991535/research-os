import { Download } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ExportOut } from "@/api/writing";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** Result of an export (doc or run): status, downloadable files and diagnostics. One component for every export. */
export function ExportResult({ label, result }: { label: string; result: ExportOut }) {
  const { t } = useTranslation();
  return <Card><CardHeader><CardTitle>{t("export.resultTitle")}</CardTitle></CardHeader><CardContent className="grid gap-2">
    <p role="status">{label} · {result.status}</p>
    <ul className="grid gap-1">{result.files.map((file) => <li key={file.path}>
      <a className="inline-flex min-h-11 items-center gap-2 text-primary underline-offset-4 hover:underline"
        href={`/api/v1/exports/${encodeURIComponent(result.id)}/files/${encodeURIComponent(file.path.split("/").pop() ?? "")}`} download>
        <Download className="size-4" />{file.path.split("/").pop()}</a>
    </li>)}</ul>
    <ul>{result.diagnostics.map((d, i) => <li key={i} className={d.severity === "error" ? "text-destructive" : "text-muted-foreground"}>
      {d.severity}: {d.code} — {d.message}{d.ref && ` (${d.ref})`}</li>)}</ul>
  </CardContent></Card>;
}
