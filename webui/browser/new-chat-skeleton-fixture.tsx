import { useState } from "react";
import { ThreadShell } from "@/components/thread/ThreadShell";
import type { WorkspaceScopePayload } from "@/lib/types";
import { StartupSkeleton } from "@/components/StartupSkeleton";
import { settings } from "@/dev/gallery/settings";
import "@/components/startup-skeleton.css";

const noop = () => {};

export function NewChatSkeletonFixture() {
  const params = new URLSearchParams(location.search);
  const [workspaceScope, setWorkspaceScope] = useState<WorkspaceScopePayload>({ project_path: "/workspace", access_mode: "full" });
  const dark = params.has("dark");
  document.documentElement.classList.toggle("dark", dark);
  return <div className="flex overflow-hidden bg-background" style={{ height: "100dvh" }}>
    {params.has("loading") ? <StartupSkeleton /> : <>
      <main className="thread-workspace relative flex min-w-0 flex-1 flex-col overflow-hidden">
        <ThreadShell session={null} title="yoreni" onToggleSidebar={noop} theme={dark ? "dark" : "light"}
          hideSidebarToggleForHostChrome onTemporaryChatEnabledChange={noop} settingsSnapshot={settings}
          workspaceScope={workspaceScope}
          workspaceDefaultScope={{ project_path: "/workspace", access_mode: "full" }}
          workspaceControls={{ can_change_project: true, can_browse_directories: true }} onWorkspaceScopeChange={setWorkspaceScope} />
      </main>
    </>}
  </div>;
}
