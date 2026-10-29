import { useState } from "react";
import { useTranslation } from "react-i18next";
import { DeleteConfirm } from "@/components/DeleteConfirm";
import { FileActions } from "@/components/FileActions";
import { RenameDialog } from "@/components/RenameDialog";
import { StartupShell } from "@/components/StartupShell";
import * as ProductIcons from "@/components/icons/product-icons";
import { Button } from "@/components/ui/button";
import type { AutomationJob } from "@/lib/types";
import { cn } from "@/lib/utils";
import { StatePicker, type ComponentExample } from "./example";
import { useGalleryTranslation } from "./i18n";

function RenamePreview() {
  const { t } = useTranslation();
  const { t: g } = useGalleryTranslation();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("组件设计");
  return <div className="space-y-4">
    <p className="text-sm">{title}</p>
    <Button variant="outline" onClick={() => setOpen(true)}>{t("workbench.renameGroupTitle")}</Button>
    <RenameDialog open={open} title={title}
      dialogTitle={t("workbench.renameGroupTitle")}
      description={t("workbench.renameGroupDescription")}
      placeholder={t("workbench.renameGroupPlaceholder")}
      onCancel={() => setOpen(false)}
      onConfirm={(next) => { setTitle(next); setOpen(false); }} />
    <p role="status" className="text-xs text-muted-foreground">{g("previews.rename.hint")}</p>
  </div>;
}

const automations: AutomationJob[] = [
  { id: "gallery-daily", name: "每日项目摘要", enabled: true, schedule: { kind: "cron", expr: "0 9 * * *", tz: "Asia/Shanghai" }, payload: { message: "Summarize project updates" }, state: { next_run_at_ms: Date.UTC(2026, 9, 14, 1) } },
  { id: "gallery-hourly", name: "每小时检查", enabled: false, schedule: { kind: "every", every_ms: 3_600_000 }, payload: { message: "Check updates" }, state: {} },
  { id: "gallery-trigger", name: "发布后检查", enabled: true, schedule: { kind: "local" }, payload: { message: "Check the release", kind: "local_trigger" }, state: {} },
  { id: "gallery-once", name: "一次性提醒", enabled: true, schedule: { kind: "at", at_ms: Date.UTC(2026, 9, 15, 2) }, payload: { message: "Review the result" }, state: { next_run_at_ms: Date.UTC(2026, 9, 15, 2) } },
  { id: "gallery-weekly", name: "每周回顾", enabled: true, schedule: { kind: "every", every_ms: 604_800_000 }, payload: { message: "Review the week" }, state: {} },
];

function DeletePreview() {
  const { t } = useGalleryTranslation();
  const [scenario, setScenario] = useState<"single" | "multiple" | "automations">("single");
  const [open, setOpen] = useState(false);
  const [deleted, setDeleted] = useState(false);
  return <>
    <StatePicker label={t("previews.delete.scenario")} value={scenario} options={["single", "multiple", "automations"]}
      onChange={(next) => { setScenario(next); setDeleted(false); }} />
    <Button variant="outline" onClick={() => { setDeleted(false); setOpen(true); }}>{t("previews.delete.open")}</Button>
    <DeleteConfirm open={open} title="组件设计" count={scenario === "multiple" ? 3 : 1}
      automations={scenario === "automations" ? automations : []} onCancel={() => setOpen(false)}
      onConfirm={() => { setDeleted(true); setOpen(false); }} />
    <p role="status" className="mt-4 text-xs text-muted-foreground">{t(deleted ? "previews.delete.confirmed" : "previews.delete.hint")}</p>
  </>;
}


function ProductIconsPreview() {
  const { t } = useGalleryTranslation();
  const [size, setSize] = useState<"16" | "20" | "24">("20");
  const [selected, setSelected] = useState("ConversationIcon");
  return <>
    <StatePicker label={t("previews.productIcons.size")} value={size} options={["16", "20", "24"]} onChange={setSize} />
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">{Object.entries(ProductIcons).map(([name, Icon]) =>
      <button key={name} type="button" aria-pressed={selected === name} onClick={() => setSelected(name)}
        className={cn("flex min-w-0 flex-col items-center gap-2 rounded-control p-3 text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", selected === name && "bg-muted text-foreground")}>
        <Icon size={Number(size)} /><span className="max-w-full break-all text-center text-[10px] leading-4">{name.replace(/Icon$/, "")}</span>
      </button>)}</div>
    <p role="status" className="mt-4 text-xs text-muted-foreground">{t("previews.productIcons.selected", { name: selected })}</p>
  </>;
}

function StartupPreview() {
  return <div className="gallery-startup-preview h-80 overflow-hidden rounded-panel border"><StartupShell embedded /></div>;
}

function FileActionsPreview() {
  const { t } = useGalleryTranslation();
  const [scope, setScope] = useState<"workspace" | "outside" | "unresolved">("workspace");
  const path = "src/app.tsx";
  return <>
    <StatePicker label={t("previews.fileActions.scope")} value={scope} options={["workspace", "outside", "unresolved"]} onChange={setScope} />
    <FileActions path={path} metadata={scope === "unresolved" ? undefined : {
      path: scope === "workspace" ? "D:/workspace/yoreni/src/app.tsx" : "D:/documents/report.tsx",
      relative_path: scope === "workspace" ? path : null,
    }}><Button variant="outline">{path}</Button></FileActions>
    <p className="mt-4 text-xs text-muted-foreground">{t("previews.fileActions.hint")}</p>
  </>;
}

export const shellExamples: ComponentExample[] = [
{ title: "previews.delete.title", description: "previews.delete.description", options: "previews.delete.options", files: ["webui/src/components/DeleteConfirm.tsx"], Preview: DeletePreview, code: "<DeleteConfirm />" },
{ title: "previews.rename.title", description: "previews.rename.description", options: "previews.rename.options", files: ["webui/src/components/RenameDialog.tsx"], Preview: RenamePreview, code: "<RenameDialog />" },
{ title: "previews.productIcons.title", description: "previews.productIcons.description", options: "previews.productIcons.options", files: ["webui/src/components/icons/product-icons.tsx"], Preview: ProductIconsPreview, code: "<icons/product-icons />" },
{ title: "previews.startup.title", description: "previews.startup.description", options: "previews.startup.options", files: ["webui/src/components/StartupShell.tsx"], Preview: StartupPreview, code: "<StartupShell />" },
{ title: "previews.fileActions.title", description: "previews.fileActions.description", options: "previews.fileActions.options", files: ["webui/src/components/FileActions.tsx"], Preview: FileActionsPreview, code: "<FileActions />" }
];
