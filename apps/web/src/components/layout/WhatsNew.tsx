// "What's new" after an update (ADR-228): the host keeps the installed release's notes until they are dismissed once.
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { describeError } from "@/api/client";
import { qk, updateCalls, useUpdateStatus } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { newKey } from "@/lib/utils";
import { releaseNotes } from "@/pages/SettingsUpdates";
import { useLanguage } from "./language";

export function WhatsNew() {
  const { t } = useTranslation();
  const lng = useLanguage();
  const qc = useQueryClient();
  const w = useUpdateStatus().data?.whats_new;
  if (!w) return null;
  const notes = releaseNotes(w, lng);
  const dismiss = () => void updateCalls.dismissWhatsNew(newKey()).then((d) => qc.setQueryData(qk.update, d))
    .catch((e: unknown) => toast.error(describeError(e)));
  return (
    <Dialog open onOpenChange={(o) => { if (!o) dismiss(); }}>
      <DialogContent title={t("update.whatsNewTitle", { version: w.version })}>
        {notes ? <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 text-body-sm">{notes}</pre>
          : <p className="text-body-sm text-muted-foreground">{t("update.whatsNewEmpty")}</p>}
        <div className="flex justify-end"><Button size="touch" onClick={dismiss}>{t("update.whatsNewOk")}</Button></div>
      </DialogContent>
    </Dialog>
  );
}
