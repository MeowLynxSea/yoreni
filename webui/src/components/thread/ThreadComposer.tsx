import { useCallback, useRef, useState, type Ref } from "react";
import { ArrowUp, Mic, Paperclip, Square, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useAttachedImages, ACCEPT_ATTR } from "@/hooks/useAttachedImages";
import { useVoiceRecorder } from "@/hooks/useVoiceRecorder";
import type { SendAttachment, SendOptions } from "@/lib/agent-input";
import type { LocalAttachment } from "../../../../packages/client-events/attachments";
import type { WebUIIngressLimits } from "@/lib/types";
import type { ModelPresetOption } from "./ModelPresetBadge";

interface Props {
  onSend: (content: string, attachments?: SendAttachment[], options?: SendOptions) => boolean | void | Promise<boolean | void>;
  onStop?: () => void;
  disabled?: boolean;
  isStreaming?: boolean;
  placeholder?: string;
  inputAriaLabel?: string;
  variant?: "thread" | "hero";
  compactWhenIdle?: boolean;
  modelLabel?: string | null;
  modelDetail?: string | null;
  modelProvider?: string | null;
  modelProviderLabel?: string | null;
  modelNeedsSetup?: boolean;
  modelPreset?: string | null;
  modelPresets?: ModelPresetOption[];
  onModelPresetChange?: (name: string) => void;
  onModelBadgeClick?: () => void;
  onManageModels?: () => void;
  onTranscribeAudio?: (dataUrl: string, options?: { durationMs?: number }) => Promise<string>;
  onInspectAttachment?: (path: string) => Promise<LocalAttachment>;
  ingressLimits?: WebUIIngressLimits | null;
  surfaceRef?: Ref<HTMLDivElement>;
}

/** Drafts and attachments belong to the current page; execution belongs to the Agent. */
export function ThreadComposer({ onSend, onStop, disabled = false, isStreaming = false, placeholder, inputAriaLabel, onTranscribeAudio, ingressLimits, surfaceRef, modelLabel, modelPreset, modelPresets, onModelPresetChange, onModelBadgeClick }: Props) {
  const { t } = useTranslation();
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const files = useRef<HTMLInputElement>(null);
  const attachments = useAttachedImages({ ingressLimits });
  const appendTranscript = useCallback((text: string) => setValue((current) => current ? `${current}\n${text}` : text), []);
  const voice = useVoiceRecorder({ disabled, onClearError: () => setError(null), onError: (key) => setError(t(`thread.composer.voice.errors.${key}`)), onTranscript: appendTranscript, onTranscribeAudio });
  const send = async () => {
    if (sending || disabled || attachments.encoding || (!value.trim() && !attachments.images.length)) return;
    const content = value;
    const media: SendAttachment[] = attachments.images.flatMap((image) => image.status !== "ready" ? [] : [{
      media: image.local ? { path: image.local.path, name: image.file.name } : { data_url: image.dataUrl!, name: image.file.name },
      preview: { kind: image.kind, url: image.previewUrl ?? image.local?.url, name: image.file.name },
    }]);
    if (media.length !== attachments.images.length) return;
    setSending(true); setError(null);
    try {
      const accepted = await onSend(content, media);
      if (accepted !== false) { setValue((current) => current === content ? "" : current); attachments.clear(); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setSending(false); }
  };
  return <div ref={surfaceRef} className="rounded-floating border border-border bg-card p-3 shadow-sm"
    onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); void attachments.enqueue(Array.from(event.dataTransfer.files)); }}>
    {attachments.images.length > 0 && <div className="mb-2 flex flex-wrap gap-2">{attachments.images.map((image) => <div key={image.id} className="flex max-w-full items-center gap-2 rounded-control bg-muted px-2 py-1 text-xs"><span className="break-all">{image.file.name}</span><Button variant="ghost" size="icon" className="h-5 w-5" aria-label={t("thread.composer.remove")} onClick={() => attachments.remove(image.id)}><X className="h-3 w-3" /></Button></div>)}</div>}
    <Textarea value={value} disabled={disabled} aria-label={inputAriaLabel ?? t("thread.composer.placeholderThread")} placeholder={placeholder ?? t("thread.composer.placeholderThread")} className="min-h-20 max-h-64 resize-none border-0 bg-transparent p-1 shadow-none focus-visible:ring-0"
      onChange={(event) => setValue(event.target.value)}
      onPaste={(event) => { if (event.clipboardData.files.length) { event.preventDefault(); void attachments.enqueue(Array.from(event.clipboardData.files)); } }}
      onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} />
    {error && <p role="alert" className="py-1 text-xs text-destructive">{error}</p>}
    <div className="flex items-center gap-2">
      <input ref={files} type="file" multiple accept={ACCEPT_ATTR} className="hidden" onChange={(event) => { void attachments.enqueue(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
      <Button variant="ghost" size="icon" aria-label={t("thread.composer.attachImage")} disabled={disabled || attachments.full} onClick={() => files.current?.click()}><Paperclip className="h-4 w-4" /></Button>
      {modelPresets?.length && onModelPresetChange ? <select aria-label={t("settings.nav.models")} className="min-w-0 max-w-52 bg-transparent text-xs" value={modelPreset ?? ""} onChange={(event) => onModelPresetChange(event.target.value)}>{modelPresets.map((preset) => <option key={preset.name} value={preset.name}>{preset.name}</option>)}</select> : modelLabel ? <button className="text-xs text-muted-foreground" onClick={onModelBadgeClick}>{modelLabel}</button> : null}
      <div className="ml-auto flex gap-2">{onTranscribeAudio && <Button variant="ghost" size="icon" aria-label={t("thread.composer.tools.voice")} disabled={voice.buttonDisabled} onClick={voice.handleClick}><Mic className="h-4 w-4" /></Button>}
      {isStreaming && onStop && <Button variant="outline" size="icon" aria-label={t("thread.composer.stop")} onClick={onStop}><Square className="h-4 w-4" /></Button>}
      <Button size="icon" aria-label={t("thread.composer.send")} disabled={disabled || sending || attachments.encoding || (!value.trim() && !attachments.images.length)} onClick={() => { void send(); }}><ArrowUp className="h-4 w-4" /></Button></div>
    </div>
  </div>;
}
