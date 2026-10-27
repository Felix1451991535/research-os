// Library, search and cited QA calls (K7 + research). Response shapes for these endpoints are dynamic dicts
// in OpenAPI, so they are typed here explicitly and kept minimal.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, OfflineError, api } from "./client";
import { runIntent } from "@/offline/intents";
import { newKey } from "@/lib/utils";

export interface DocRow {
  version_id: string; work_id: string; title: string; original_name: string; parse_state: string;
  parse_error: string | null; recovery_action: string | null; page_count: number | null; doi: string | null;
  task_state: string | null; created_at: string;
}
export interface Passage { label: string; chunk_id: string; version_id: string; title: string; page_index: number; text: string; quality: string }
export interface Sentence { text: string; kind: string; evidence: string[]; evidence_ids?: string[] }
export interface Answer {
  id: string; status: string; question: string; sentences: Sentence[]; dropped: (Sentence & { reason: string })[];
  passages: Passage[]; labels?: string[]; model?: string; reason?: string; trust: Record<string, number>; created_at: string;
}
export interface CatalogRow { alias: string; provider: string; model_id: string; tier: string; usable: boolean; reason: string | null; certification: string }

export const useDocuments = (projectId?: string) => useQuery({
  queryKey: ["documents", projectId ?? "all"],
  queryFn: ({ signal }) => api.get<DocRow[]>(`/documents${projectId ? `?project_id=${projectId}` : ""}`, signal),
  refetchInterval: 4000,
});
export const useAnswers = (projectId?: string) => useQuery({
  queryKey: ["answers", projectId ?? "all"],
  queryFn: ({ signal }) => api.get<Answer[]>(`/answers${projectId ? `?project_id=${projectId}` : ""}`, signal),
});
export const useCatalog = () => useQuery({ queryKey: ["catalog"], queryFn: ({ signal }) => api.get<CatalogRow[]>("/gateway/catalog", signal) });

export async function uploadDocument(file: File, projectId?: string): Promise<{ version_id: string; created: boolean; parse_state: string }> {
  const fd = new FormData();
  fd.append("file", file);
  if (projectId) fd.append("project_id", projectId);
  let res: Response;
  try {
    res = await fetch("/api/v1/documents/upload", { method: "POST", body: fd, credentials: "same-origin",
      headers: { "x-researchos-client": "web", "idempotency-key": newKey() } });
  } catch {
    throw new OfflineError();
  }
  if (!res.ok) throw new ApiError(res.status, await res.json());
  return res.json();
}

export function useAsk() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { question: string; project_id?: string; force_top: boolean; intentId: string }) =>
      runIntent("answer.create", v.intentId, v, (key) =>
        api.post<Answer>("/answers", { question: v.question, project_id: v.project_id ?? null, force_top: v.force_top },
          { idempotencyKey: key })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["answers"] }),
  });
}

// Literature request aliases come from generated OpenAPI; dynamic reads mirror literature services.
import type { LiteratureSearchInput, CitationChainInput, LiteratureRun, ReviewInput, ReviewItemsInput,
  ScreeningInput, LiteratureReview, SavedSearchInput, SavedSearch, VenueTiersInput, LibraryWork, Task } from "./types";
const segment = encodeURIComponent;
export const literature = {
  search: (body: LiteratureSearchInput, key: string) => api.post<LiteratureRun>("/literature/search", body, { idempotencyKey: key }),
  chain: (body: CitationChainInput, key: string) => api.post<LiteratureRun>("/literature/chain", body, { idempotencyKey: key }),
  createReview: (body: ReviewInput, key: string) => api.post<LiteratureReview>("/reviews", body, { idempotencyKey: key }),
  addItems: (id: string, body: ReviewItemsInput, key: string) => api.post(`/reviews/${segment(id)}/items`, body, { idempotencyKey: key }),
  decide: (id: string, body: ScreeningInput, key: string) => api.post(`/reviews/${segment(id)}/decisions`, body, { idempotencyKey: key }),
  save: (body: SavedSearchInput, key: string) => api.post<SavedSearch>("/saved-searches", body, { idempotencyKey: key }),
  enable: (id: string, enabled: boolean, key: string) => api.put(`/saved-searches/${segment(id)}/enabled`, { enabled }, { idempotencyKey: key }),
  run: (id: string, key: string) => api.post<Task>(`/saved-searches/${segment(id)}/run`, {}, { idempotencyKey: key }),
  tiers: (body: VenueTiersInput, key: string) => api.put("/library/venue-tiers", body, { idempotencyKey: key }),
  checkRetraction: (id: string, key: string) => api.post<Record<string, unknown>>("/literature/retractions/check", { work_ids: [id] }, { idempotencyKey: key }),
};
export const useSavedSearches = () => useQuery({ queryKey: ["saved-searches"], queryFn: ({ signal }) => api.get<SavedSearch[]>("/saved-searches", signal) });
export const useLiteratureReview = (id: string) => useQuery({ queryKey: ["literature-review", id], queryFn: ({ signal }) => api.get<LiteratureReview>(`/reviews/${segment(id)}`, signal), refetchInterval: 4000 });
export const usePrisma = (id: string) => useQuery({ queryKey: ["prisma", id], queryFn: ({ signal }) => api.get<Record<string, unknown>>(`/reviews/${segment(id)}/prisma`, signal), refetchInterval: 4000 });
export const useLibraryWorks = () => useQuery({ queryKey: ["library-works"], queryFn: ({ signal }) => api.get<LibraryWork[]>("/library/works?limit=1000", signal) });
