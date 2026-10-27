import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { qk } from "./queries";
import type { Note } from "./types";
import { runIntent } from "@/offline/intents";

export function useCreateNote(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { title: string; body: string; client_operation_id: string }) =>
      runIntent("note.create", b.client_operation_id, b, (key) =>
        api.post<Note>(`/projects/${projectId}/notes`, b, { idempotencyKey: key })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.notes(projectId) });
      void qc.invalidateQueries({ queryKey: qk.events(projectId) });
    },
  });
}

export function useUpdateNote(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (b: { id: string; title: string; body: string; base_version: number; client_operation_id: string }) =>
      runIntent("note.update", `${b.client_operation_id}:${b.base_version}`, b, (key) =>
        api.put<Note>(`/notes/${b.id}`, { title: b.title, body: b.body, base_version: b.base_version,
          client_operation_id: b.client_operation_id }, { idempotencyKey: key })),
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.notes(projectId) });
      void qc.invalidateQueries({ queryKey: qk.events(projectId) });
    },
  });
}
