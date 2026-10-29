// Authored docs (M6): reports/manuscripts with placeholders, live citation + number gates, suggestions, export.
// Response shapes are dynamic dicts in OpenAPI, so they are typed here explicitly and kept minimal.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import i18n from "@/i18n";
import type { components } from "./schema";
type ExportInput = components["schemas"]["researchos__api__routers__docs__ExportIn"];

export interface WDoc { id: string; project_id: string; kind: string; title: string; body: string; version: number; updated_at: string }
export interface Diagnostic { severity: "info" | "warning" | "error"; code: string; message: string; ref: string | null }
export interface GateReport { ok: boolean; mode: string; counts: Record<string, number>; diagnostics: Diagnostic[] }
export interface Suggestion { id: string; op: string; anchor: string | null; text: string; rationale: string; author: string; status: string }
export interface ExportOut { id: string; status: string; files: { path: string; mime: string; sha256: string }[]; diagnostics: Diagnostic[] }

export const useDocs = (pid: string) => useQuery({
  queryKey: ["wdocs", pid], queryFn: ({ signal }) => api.get<WDoc[]>(`/projects/${pid}/docs`, signal),
});
export const useDoc = (id: string) => useQuery({ queryKey: ["wdoc", id], queryFn: ({ signal }) => api.get<WDoc>(`/docs/${id}`, signal) });
export const useGates = (id: string, version: number | undefined) => useQuery({
  queryKey: ["gates", id, version], enabled: version !== undefined,
  queryFn: ({ signal }) => api.get<GateReport>(`/docs/${id}/check?mode=final`, signal),
});
export const useSuggestions = (id: string) => useQuery({
  queryKey: ["suggestions", id], queryFn: ({ signal }) => api.get<Suggestion[]>(`/docs/${id}/suggestions?status=proposed`, signal),
});

/** Docs shown in a project's Docs card: notes have their own card. */
export function listedDocs(docs: WDoc[]): WDoc[] {
  return docs.filter((d) => d.kind !== "note");
}

/** Accepting a suggestion creates a new revision and the editor reloads from it, so it is blocked while the
 * editor holds unsaved changes (they would be discarded). */
export function acceptBlockedReason(dirty: boolean): string | null {
  return dirty ? i18n.t("doc.acceptBlocked") : null;
}

export function useCreateDoc(pid: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { kind: "report" | "manuscript"; title: string; key: string }) =>
      api.post<WDoc>(`/projects/${pid}/docs`, { kind: v.kind, title: v.title, body: "" }, { idempotencyKey: v.key }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["wdocs", pid] }),
  });
}

export function useSaveDoc(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { title: string; body: string; base_version: number; key: string }) =>
      api.put<WDoc>(`/docs/${id}`, { title: v.title, body: v.body, base_version: v.base_version }, { idempotencyKey: v.key }),
    onSuccess: (d) => { qc.setQueryData(["wdoc", id], d); void qc.invalidateQueries({ queryKey: ["gates", id] }); },
  });
}

export function useDecide(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { sid: string; accept: boolean; key: string }) =>
      api.post<{ status: string }>(`/suggestions/${v.sid}/decision`, { accept: v.accept }, { idempotencyKey: v.key }),
    onSuccess: async () => {
      await Promise.all(["suggestions", "wdoc", "gates"].map((k) => qc.invalidateQueries({ queryKey: [k, id] })));
    },
  });
}

/** One-click export of experiment results (a run) through the same exporters as documents (ADR-165). */
export function useRunExport(runId: string) {
  return useMutation({
    mutationFn: (v: { exporter: string; language: string; key: string }) =>
      api.post<ExportOut>(`/exports/${v.exporter}`, { run_id: runId, mode: "final", options: { language: v.language } },
        { idempotencyKey: v.key }),
  });
}

export function useExport(id: string) {
  return useMutation({
    mutationFn: (v: ExportInput & { language: string; key: string }) =>
      api.post<ExportOut>(`/docs/${id}/export`, { exporter: v.exporter, mode: v.mode, options: { language: v.language } },
        { idempotencyKey: v.key }),
  });
}

export type CardKind = "card" | "methods-checklist";
export interface CardOut { doc: WDoc; fields: Record<string, { cited?: boolean; status?: string }> }
/** Per-paper cited cards (paper card, methods checklist): paid cited-QA calls, ledger-capped; one doc per intent. */
export const createCard = (kind: CardKind, workId: string, projectId: string, key: string) =>
  api.post<CardOut>(`/works/${encodeURIComponent(workId)}/${kind}`, { project_id: projectId }, { idempotencyKey: key });

interface BundleOut { id: string; status: string; files: ExportOut["files"]; runs: number; failed_runs: number;
  skipped: { file: string; reason: string }[] }
/** Reproducible bundle of every run of a plan (ADR-192/203); shown with the shared export result. */
export function usePlanBundle(planId: string) {
  return useMutation({
    mutationFn: async (key: string): Promise<ExportOut & { runs: number; failedRuns: number }> => {
      const b = await api.post<BundleOut>(`/plans/${encodeURIComponent(planId)}/bundle`, {}, { idempotencyKey: key });
      return { id: b.id, status: b.status, files: b.files, runs: b.runs, failedRuns: b.failed_runs,
        diagnostics: b.skipped.map(s => ({ severity: "info" as const, code: "left_out", message: s.reason, ref: s.file })) };
    },
  });
}
