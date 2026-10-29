import { useState } from "react";

import { FilePreviewPanelView, type FilePreviewPanelState } from "@/components/FilePreviewPanelView";
import { StatePicker, type ComponentExample } from "./example";
import { useGalleryTranslation } from "./i18n";

function FilePreview() {
  const { t } = useGalleryTranslation();
  const [mode, setMode] = useState<"markdown" | "text" | "image" | "truncated" | "loading" | "error" | "route_missing">("markdown");
  const [imageOpen, setImageOpen] = useState(false);
  const path = mode === "image" ? "yoreni.png" : mode === "markdown" || mode === "truncated" ? "REPORT.md" : "greeting.ts";
  const state: FilePreviewPanelState = mode === "loading" ? { status: "loading" }
    : mode === "error" || mode === "route_missing" ? { status: "error", routeMissing: mode === "route_missing" }
      : { status: "ready", payload: mode === "image"
        ? { kind: "image", path, display_path: path, project_path: "/workspace", size: 24_576, mime_type: "image/png", data_url: "/brand/yoreni_icon_512.png" }
        : { path, display_path: path, project_path: "/workspace", size: 128, language: path.endsWith(".md") ? "markdown" : "typescript", truncated: mode === "truncated",
          content: path.endsWith(".md")
            ? '# Skills 迁移报告\n\n已完成迁移，**原文件保持不变**。\n\n## 验证结果\n\n| 项目 | 结果 |\n| --- | --- |\n| 文件数量 | 1,199 个 |\n| 校验 | SHA-256 一致 |\n\n- [x] 保留原目录\n- [x] 检查引用\n\n## 下一步\n\n打开技能目录，确认所需服务已连接。\n\n```sh\nls skills/\n```\n\n![示意图](https://example.com/chart.png)\n'
            : 'export const greeting = "Hello, Yoreni";\n\nexport function greet(name: string) {\n  return `${greeting}, ${name}`;\n}\n' } };
  return <div className="space-y-4">
    <StatePicker label={t("previews.filePreview.state")} value={mode} options={["markdown", "text", "image", "truncated", "loading", "error", "route_missing"]}
      onChange={(next) => { setMode(next); setImageOpen(false); }} />
    <div className="flex h-[32rem] flex-col overflow-hidden rounded-control border">
      <FilePreviewPanelView key={path} path={path} state={state} imageOpen={imageOpen} onImageOpenChange={setImageOpen}
        onImageError={() => { setMode("error"); setImageOpen(false); }} />
    </div>
  </div>;
}

export const contentExamples: ComponentExample[] = [
{ title: "previews.filePreview.title", files: ["webui/src/components/FilePreviewPanelView.tsx"], Preview: FilePreview,
    description: "previews.filePreview.description", options: "previews.filePreview.options",
    code: '<FilePreviewPanelView path={path} state={previewState} imageOpen={imageOpen} onImageOpenChange={setImageOpen} onImageError={reportImageError} />' }
];
