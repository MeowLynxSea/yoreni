import { ChevronRight, FolderKanban, Pencil, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

import { ProjectDialog, type ProjectDraft } from "@/components/projects/ProjectDialog";
import { ProjectEmoji } from "@/components/projects/ProjectEmoji";
import { DeleteConfirm } from "@/components/DeleteConfirm";
import { Button } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/input";
import { currentLocale } from "@/i18n";
import { fetchProjects } from "@/lib/api";
import { fmtDateTime } from "@/lib/format-time";
import type { ProjectSummary } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

type DialogState = { mode: "create" } | { mode: "edit"; project: ProjectSummary } | null;

/** Every project the agent runs, with the actions that manage them. */
export function ProjectsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { client, getToken } = useClient();
  const locale = currentLocale();
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [query, setQuery] = useState("");
  const [dialog, setDialog] = useState<DialogState>(null);
  const [deleting, setDeleting] = useState<ProjectSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setProjects(null);
    setLoadFailed(false);
    try {
      const payload = await fetchProjects(getToken());
      setProjects(payload.projects);
    } catch {
      setProjects([]);
      setLoadFailed(true);
    }
  }, [getToken]);

  useEffect(() => { void load(); }, [load]);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const all = projects ?? [];
    return needle ? all.filter((project) => project.name.toLowerCase().includes(needle)) : all;
  }, [projects, query]);

  const submit = async (draft: ProjectDraft) => {
    if (!dialog || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      if (dialog.mode === "create") {
        const created = await client.requestMutation<{ project: ProjectSummary }>("project.create", {
          name: draft.name,
          description: draft.description,
          emoji: draft.emoji,
        });
        setProjects((current) => [created.project, ...(current ?? [])]);
        setDialog(null);
        navigate(`/projects/${created.project.id}`, { viewTransition: true });
      } else {
        const updated = await client.requestMutation<{ project: ProjectSummary }>("project.update", {
          project_id: dialog.project.id,
          name: draft.name,
          description: draft.description,
          emoji: draft.emoji,
        });
        setProjects((current) => (current ?? []).map((project) => (
          project.id === updated.project.id ? updated.project : project
        )));
        setDialog(null);
      }
    } catch {
      setActionError(t("projects.saveFailed"));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!deleting || busy) return;
    setBusy(true);
    setActionError(null);
    try {
      await client.requestMutation("project.delete", { project_id: deleting.id });
      setProjects((current) => (current ?? []).filter((project) => project.id !== deleting.id));
      setDeleting(null);
    } catch {
      setActionError(t("projects.deleteFailed"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-[34rem] px-5 pb-16 pt-2 sm:pt-6">
        <div className="flex items-center justify-between gap-3 px-1">
          <h1 className="display-serif text-[1.7rem] leading-tight text-foreground/95">
            {t("projects.title")}
          </h1>
          <Button size="sm" className="shrink-0" onClick={() => { setActionError(null); setDialog({ mode: "create" }); }}>
            <Plus className="mr-1 h-4 w-4" strokeWidth={1.9} aria-hidden />
            {t("projects.new")}
          </Button>
        </div>

        <div className="mt-4">
          <SearchInput
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("projects.search")}
            aria-label={t("projects.searchAria")}
          />
        </div>

        {actionError ? (
          <p role="alert" className="mt-3 rounded-2xl bg-destructive/10 px-4 py-2 text-[12.5px] text-destructive">
            {actionError}
          </p>
        ) : null}

        {projects === null ? (
          <div role="status" aria-busy="true" aria-label={t("projects.loading")} className="settings-sheet mt-3 space-y-3 p-5">
            <div className="thread-skeleton-bar h-4 w-1/2" />
            <div className="thread-skeleton-bar h-3 w-3/4" />
          </div>
        ) : loadFailed ? (
          <div className="settings-sheet mt-3 flex items-center justify-between gap-3 px-5 py-4">
            <p className="text-[13px] leading-5 text-muted-foreground">{t("projects.loadFailed")}</p>
            <Button variant="outline" size="sm" className="shrink-0" onClick={() => { void load(); }}>
              {t("common.retry")}
            </Button>
          </div>
        ) : visible.length === 0 ? (
          <div className="mt-8 px-6 py-8 text-center">
            <p className="text-[15px] text-foreground/90">
              {query.trim() ? t("projects.noMatches", { query: query.trim() }) : t("projects.empty")}
            </p>
            {query.trim() ? null : (
              <p className="mt-1.5 text-[12.5px] leading-5 text-muted-foreground">{t("projects.emptyHint")}</p>
            )}
          </div>
        ) : (
          <ul className="mt-2 space-y-1">
            {visible.map((project) => (
              <li key={project.id} className="flex items-center gap-1 rounded-[1.35rem] pr-2 transition-colors duration-150 hover:bg-foreground/[0.05] dark:hover:bg-white/[0.06]">
                <button
                  type="button"
                  onClick={() => navigate(`/projects/${project.id}`, { viewTransition: true })}
                  className="group flex min-w-0 flex-1 items-center gap-4 rounded-[1.35rem] px-3 py-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/35"
                >
                  <span className="grid h-11 w-11 shrink-0 place-items-center rounded-[1rem] bg-foreground/[0.06] text-foreground/70">
                    {project.emoji ? (
                      <ProjectEmoji emoji={project.emoji} className="h-6 w-6" />
                    ) : (
                      <FolderKanban className="h-[19px] w-[19px]" strokeWidth={1.6} aria-hidden />
                    )}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[18px] font-medium leading-7 text-foreground/95">{project.name}</span>
                    <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-muted-foreground/75">
                      <span>{t("projects.updated", { time: fmtDateTime(project.updated_at_ms, locale) })}</span>
                      {project.active_runs > 0 ? (
                        <span className="rounded-full bg-[#bf5a42]/12 px-2 py-0.5 text-[#bf5a42] dark:bg-[#d98770]/15 dark:text-[#e39a85]">
                          {t("projects.running")}
                        </span>
                      ) : null}
                    </span>
                  </span>
                  <ChevronRight
                    className="h-[18px] w-[18px] shrink-0 text-muted-foreground/50 transition-transform duration-150 group-hover:translate-x-0.5 motion-reduce:transition-none"
                    aria-hidden
                  />
                </button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t("projects.renameAria", { name: project.name })}
                  title={t("chat.rename")}
                  onClick={() => { setActionError(null); setDialog({ mode: "edit", project }); }}
                >
                  <Pencil className="h-4 w-4" strokeWidth={1.7} aria-hidden />
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={t("projects.deleteAria", { name: project.name })}
                  title={t("chat.delete")}
                  onClick={() => { setActionError(null); setDeleting(project); }}
                >
                  <Trash2 className="h-4 w-4 text-destructive" strokeWidth={1.7} aria-hidden />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <ProjectDialog
        open={dialog !== null}
        mode={dialog?.mode ?? "create"}
        initial={dialog?.mode === "edit" ? {
          name: dialog.project.name,
          description: dialog.project.description,
          emoji: dialog.project.emoji,
        } : undefined}
        busy={busy}
        onCancel={() => setDialog(null)}
        onSubmit={(draft) => { void submit(draft); }}
      />
      <DeleteConfirm
        open={deleting !== null}
        title={deleting?.name ?? ""}
        titleText={t("projects.deleteTitle")}
        descriptionText={t("projects.deleteDescription")}
        onCancel={() => setDeleting(null)}
        onConfirm={() => { void remove(); }}
      />
    </div>
  );
}
