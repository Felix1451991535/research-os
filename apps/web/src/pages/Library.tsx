// Library (M1, functional; visual polish deferred by owner). Shows real parse state, failure reason and recovery.
import { LibraryEvidence } from "@/components/LibraryEvidence";
import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { describeError } from "@/api/client";
import { uploadDocument, useDocuments } from "@/api/knowledge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { PageHeader } from "@/components/layout/AppShell";
import { EmptyState, QueryView } from "@/components/layout/state";

const TONE: Record<string, "success" | "warning" | "danger" | "info" | "neutral"> = {
  parsed: "success", partial: "warning", needs_ocr: "warning", failed: "danger", unsupported: "danger",
  pending: "info", parsing: "info",
};

export function LibraryPage() {
  const { t } = useTranslation();
  const q = useDocuments();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setBusy(true);
    for (const f of Array.from(files)) {
      try {
        const r = await uploadDocument(f);
        toast.success(r.created ? t("library.imported", { name: f.name }) : t("library.duplicate", { name: f.name }));
      } catch (e) { toast.error(`${f.name}: ${describeError(e)}`); }
    }
    setBusy(false);
    void q.refetch();
  };
  return (
    <>
      <PageHeader title={t("library.title")} description={t("library.description")}
        actions={<><input ref={input} type="file" accept="application/pdf" multiple hidden onChange={(e) => void onFiles(e.target.files)} />
          <Button loading={busy} onClick={() => input.current?.click()}><Upload />{t("library.import")}</Button></>} />
      <QueryView q={q}>
        {(docs) => docs.length === 0 ? <EmptyState title={t("library.emptyTitle")} body={t("library.emptyBody")} /> : (
          <div className="grid gap-2">
            {docs.map((d) => (
              <Card key={d.version_id} className="grid gap-1 p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{d.title || d.original_name}</span>
                  <Badge tone={TONE[d.parse_state] ?? "neutral"}>{d.parse_state}</Badge>
                  {d.doi ? <span className="font-mono text-caption text-muted-foreground">doi:{d.doi}</span> : null}
                </div>
                <div className="text-caption text-muted-foreground">{t("library.pages", { name: d.original_name, count: d.page_count ?? "?" })}</div>
                {d.parse_error ? <div className="text-body-sm text-destructive">{d.parse_error}</div> : null}
                {d.recovery_action ? <div className="text-body-sm">{t("library.whatToDo", { action: d.recovery_action })}</div> : null}
              </Card>
            ))}
          </div>
        )}
      </QueryView>
      <LibraryEvidence />
    </>
  );
}
