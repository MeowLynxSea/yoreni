import { useEffect, useState } from "react";

import { fetchProjects } from "@/lib/api";
import type { ProjectSummary } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

/** One project's record, read from the gateway's project list. */
export function useProject(projectId: string | null): ProjectSummary | null {
  const { getToken } = useClient();
  const [project, setProject] = useState<ProjectSummary | null>(null);
  useEffect(() => {
    setProject(null);
    if (!projectId) return;
    let live = true;
    fetchProjects(getToken()).then(
      (payload) => {
        if (live) setProject(payload.projects.find((item) => item.id === projectId) ?? null);
      },
      () => {
        // A deleted project is reported by the transcript request; the heading
        // simply stays empty until the reader is moved out.
      },
    );
    return () => { live = false; };
  }, [getToken, projectId]);
  return project;
}
