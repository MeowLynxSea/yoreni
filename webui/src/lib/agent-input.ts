import type { DraftAttachment } from "../../../packages/client-events/attachments";
import type { OutboundCliAppMention, OutboundMcpPresetMention, UIMediaAttachment, WorkspaceScopePayload } from "./types";
export interface SendAttachment { media: DraftAttachment; preview: UIMediaAttachment; }
export interface SendOptions {
  intent?: "create_automation";
  cliApps?: OutboundCliAppMention[];
  mcpPresets?: OutboundMcpPresetMention[];
  quotedContext?: string;
  workspaceScope?: WorkspaceScopePayload | null;
}
