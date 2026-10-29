import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ConnectionBadge } from "@/components/ConnectionBadge";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { LinearAvatar } from "../../../../yoreni/channels/linear/webui/LinearAvatar";
import { LinearHelpContent } from "../../../../yoreni/channels/linear/webui/LinearHelpContent";
import { LinearSecretField } from "../../../../yoreni/channels/linear/webui/LinearSecretField";
import { WebSocketIcon } from "../../../../yoreni/channels/websocket/webui/WebSocketIcon";
import type { ConnectionStatus } from "@/lib/types";
import { Sample, StatePicker, type ComponentExample } from "./example";
import { useGalleryTranslation } from "./i18n";

function ConnectionPreview() {
  const { t } = useGalleryTranslation();
  const [status, setStatus] = useState<ConnectionStatus>("open");
  return <><StatePicker label={t("previews.connectionBadge.state")} value={status} options={["idle", "connecting", "open", "reconnecting", "closed", "error"]} onChange={setStatus} />
    <div className="flex items-center gap-3"><ConnectionBadge status={status} /><span className="text-sm">{t(`previews.connectionBadge.${status}`)}</span></div></>;
}


function ChannelMarksPreview() {
  const { t } = useGalleryTranslation();
  const [name, setName] = useState<"Yoreni" | "研发">("Yoreni");
  return <><StatePicker label={t("previews.channelMarks.name")} value={name} options={["Yoreni", "研发"]} onChange={setName} />
    <div className="flex items-start gap-8"><Sample label={t("previews.channelMarks.member")}><LinearAvatar name={name} /></Sample>
      <Sample label={t("previews.channelMarks.workspace")}><LinearAvatar name={name} workspace /></Sample><Sample label="WebSocket"><WebSocketIcon /></Sample></div></>;
}

function SecretPreview() {
  const { t } = useTranslation();
  const { t: g } = useGalleryTranslation();
  const [mode, setMode] = useState<"new" | "saved" | "disabled" | "error">("new");
  const [configured, setConfigured] = useState(false);
  return <><StatePicker label={g("previews.secret.state")} value={mode} options={["new", "saved", "disabled", "error"]}
    onChange={(next) => { setMode(next); setConfigured(next === "saved"); }} />
    <LinearSecretField field={{ key: "api_key", label: t("settings.byok.apiKey"), secret: true }} configured={configured} disabled={mode === "disabled"}
      onSave={async () => { if (mode === "error") throw new Error(g("previews.secret.error")); setConfigured(true); }} />
    <p className="mt-4 text-xs text-muted-foreground">{g("previews.secret.hint")}</p></>;
}

function ChannelHelpPreview() {
  const { t } = useTranslation();
  const { t: g } = useGalleryTranslation();
  return <DropdownMenu><DropdownMenuTrigger asChild><Button variant="outline">{g("previews.channelHelp.open")}</Button></DropdownMenuTrigger>
    <DropdownMenuContent><LinearHelpContent /><DropdownMenuItem>{t("common.close")}</DropdownMenuItem></DropdownMenuContent></DropdownMenu>;
}

const components = "webui/src/components/";
export const standaloneExamples: ComponentExample[] = [
{ title: "previews.connectionBadge.title", files: [`${components}ConnectionBadge.tsx`], Preview: ConnectionPreview,
    description: "previews.connectionBadge.description", options: "previews.connectionBadge.options",
    code: '<ConnectionBadge status={status} />' },
{ title: "previews.language.title", files: [`${components}LanguageSwitcher.tsx`], Preview: LanguageSwitcher,
    description: "previews.language.description", options: "previews.language.options",
    code: '<LanguageSwitcher />' },
{ title: "previews.channelMarks.title", files: ["yoreni/channels/linear/webui/LinearAvatar.tsx", "yoreni/channels/websocket/webui/WebSocketIcon.tsx"], Preview: ChannelMarksPreview,
    description: "previews.channelMarks.description", options: "previews.channelMarks.options",
    code: '<LinearAvatar name="Yoreni" />\n<LinearAvatar name="Yoreni" workspace />\n<WebSocketIcon />' },
{ title: "previews.secret.title", files: ["yoreni/channels/linear/webui/LinearSecretField.tsx"], Preview: SecretPreview,
    description: "previews.secret.description", options: "previews.secret.options",
    code: '<LinearSecretField field={field} configured={configured} disabled={false} onSave={save} />' },
{ title: "previews.channelHelp.title", files: ["yoreni/channels/linear/webui/LinearHelpContent.tsx"], Preview: ChannelHelpPreview,
    description: "previews.channelHelp.description", options: "previews.channelHelp.options",
    code: '<DropdownMenuContent><LinearHelpContent /></DropdownMenuContent>' }
];
