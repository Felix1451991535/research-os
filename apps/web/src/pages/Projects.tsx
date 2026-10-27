import { useCallback, useState, type FormEvent } from "react";
import { Link } from "react-router";
import { FolderKanban, Plus } from "lucide-react";
import { toast } from "sonner";
import { useTranslation } from "react-i18next";
import { describeError } from "@/api/client";
import { useCreateProject, useProjects } from "@/api/queries";
import type { Project } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Input, Textarea } from "@/components/ui/input";
import { PageHeader } from "@/components/layout/AppShell";
import { EmptyState, QueryView } from "@/components/layout/state";
import { useNewShortcut } from "@/lib/shortcuts";
import { newKey, relativeTime } from "@/lib/utils";

function NewProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const { t } = useTranslation();
  const create = useCreateProject();
  const [intentId, setIntentId] = useState(newKey);
  const [f, setF] = useState({ title: "", goal: "" });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate({ ...f, intentId }, {
      onSuccess: (p) => {
        toast.success(t("projects.created", { title: p.title }));
        setF({ title: "", goal: "" });
        setIntentId(newKey()); // next submission is a new intent; retries of this one reuse the old key
        onOpenChange(false);
      },
      onError: (err) => toast.error(describeError(err)),
    });
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title={t("projects.newTitle")} description={t("projects.newDescription")}>
        <form className="grid gap-4" onSubmit={submit}>
          <Field label={t("projects.fieldTitle")} htmlFor="pt">
            <Input id="pt" autoFocus required maxLength={200} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
          </Field>
          <Field label={t("projects.fieldGoal")} htmlFor="pg" hint={t("projects.goalHint")}>
            <Textarea id="pg" maxLength={4000} value={f.goal} onChange={(e) => setF({ ...f, goal: e.target.value })} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
            <Button type="submit" loading={create.isPending}>{t("projects.create")}</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ProjectCard({ p }: { p: Project }) {
  const { t } = useTranslation();
  return (
    <Link to={`/projects/${p.id}`} className="block focus-visible:outline-none">
      <Card className="h-full p-5 transition-colors hover:border-primary/40 hover:bg-accent/30">
        <div className="mb-2 flex items-start justify-between gap-2">
          <h2 className="text-title font-semibold leading-snug">{p.title}</h2>
          {p.status !== "active" ? <Badge>{p.status}</Badge> : null}
        </div>
        <p className="line-clamp-3 text-body-sm text-muted-foreground">{p.goal || t("projects.noGoal")}</p>
        {p.next_step ? <p className="mt-3 text-body-sm"><span className="font-medium">{t("projects.next")}</span>{p.next_step}</p> : null}
        <p className="mt-3 text-caption text-muted-foreground">{t("projects.updated", { when: relativeTime(p.updated_at) })}</p>
      </Card>
    </Link>
  );
}

export function ProjectsPage() {
  const { t } = useTranslation();
  const q = useProjects();
  const [open, setOpen] = useState(false);
  useNewShortcut(useCallback(() => setOpen(true), []));
  return (
    <>
      <PageHeader title={t("projects.title")} description={t("projects.description")}
        actions={<Button onClick={() => setOpen(true)}><Plus />{t("projects.new")}</Button>} />
      <QueryView q={q}>
        {(list) => list.length === 0 ? (
          <EmptyState icon={<FolderKanban />} title={t("projects.emptyTitle")}
            body={t("projects.emptyBody")}
            action={<Button onClick={() => setOpen(true)}><Plus />{t("projects.createFirst")}</Button>} />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{list.map((p) => <ProjectCard key={p.id} p={p} />)}</div>
        )}
      </QueryView>
      <NewProjectDialog open={open} onOpenChange={setOpen} />
    </>
  );
}
