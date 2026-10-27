import type { ParseKeys, TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import type { TaskState } from "@/api/types";

const MAP: Record<string, { tone: "neutral" | "info" | "success" | "warning" | "danger" | "primary"; label: ParseKeys }> = {
  draft: { tone: "neutral", label: "status.draft" },
  awaiting_approval: { tone: "warning", label: "status.awaiting_approval" },
  queued: { tone: "info", label: "status.queued" },
  running: { tone: "primary", label: "status.running" },
  cancelling: { tone: "warning", label: "status.cancelling" },
  succeeded: { tone: "success", label: "status.succeeded" },
  failed: { tone: "danger", label: "status.failed" },
  cancelled: { tone: "neutral", label: "status.cancelled" },
  interrupted: { tone: "warning", label: "status.interrupted" },
  blocked: { tone: "danger", label: "status.blocked" },
};

/** Translated label of a task/run state; an unknown state is shown as sent. */
export function stateLabel(t: TFunction, state: string): string {
  const m = MAP[state];
  return m ? t(m.label) : state;
}

export function StatusBadge({ state }: { state: TaskState | string }) {
  const { t } = useTranslation();
  const m = MAP[state];
  return m ? <Badge tone={m.tone}>{t(m.label)}</Badge> : <Badge tone="neutral">{state}</Badge>;  // unknown state: as sent
}
