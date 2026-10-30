import { useCallback, useEffect, useRef, useState, type Ref } from "react";
import { ArrowUp, ChevronDown, Mic, Paperclip, Square, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useAttachedImages, ACCEPT_ATTR } from "@/hooks/useAttachedImages";
import { useVoiceRecorder } from "@/hooks/useVoiceRecorder";
import { readComposerDraft, writeComposerDraft } from "@/lib/local-ui-state";
import { cn } from "@/lib/utils";
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
  /** Restore and keep the unsent text across reloads and route changes. */
  persistDraft?: boolean;
}

/** The composer owns its text and attachments; execution belongs to the Agent. */
export function ThreadComposer({ onSend, onStop, disabled = false, isStreaming = false, placeholder, inputAriaLabel, variant = "thread", onTranscribeAudio, ingressLimits, surfaceRef, modelLabel, modelPreset, modelPresets, onModelPresetChange, onModelBadgeClick, persistDraft = false }: Props) {
  const { t } = useTranslation();
  const [value, setValue] = useState(() => (persistDraft ? readComposerDraft() : ""));
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const files = useRef<HTMLInputElement>(null);
  const attachments = useAttachedImages({ ingressLimits });
  useEffect(() => {
    if (persistDraft) writeComposerDraft(value);
  }, [persistDraft, value]);
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
  return <div ref={surfaceRef} data-variant={variant}
    className={cn(
      "thread-composer-surface group relative rounded-[1.75rem] p-2 transition-[border-color,box-shadow] duration-200 motion-reduce:transition-none",
      variant === "hero" && "rounded-[2rem]",
    )}
    onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); void attachments.enqueue(Array.from(event.dataTransfer.files)); }}>
    {attachments.images.length > 0 && <div className="mb-1 flex flex-wrap gap-2 px-1 pt-1">{attachments.images.map((image) => <div key={image.id} className="flex max-w-full items-center gap-1.5 rounded-full border border-white/60 bg-white/45 py-1 pl-3 pr-1 text-xs text-foreground backdrop-blur-md dark:border-white/10 dark:bg-white/[0.06]"><span className="max-w-48 truncate">{image.file.name}</span><Button variant="ghost" size="icon" className="h-5 w-5 rounded-full" aria-label={t("thread.composer.remove")} onClick={() => attachments.remove(image.id)}><X className="h-3 w-3" /></Button></div>)}</div>}
    <Textarea value={value} disabled={disabled} aria-label={inputAriaLabel ?? t("thread.composer.inputAria")}
      placeholder={placeholder ?? t(variant === "hero" ? "thread.composer.placeholderHero" : "thread.composer.placeholderThread")}
      className={cn(
        "max-h-64 resize-none border-0 bg-transparent px-3 pt-2.5 text-composer shadow-none placeholder:text-muted-foreground/75 focus-visible:ring-0",
        variant === "hero" ? "min-h-[4.25rem] pb-1" : "min-h-[3.5rem] pb-1",
      )}
      onChange={(event) => setValue(event.target.value)}
      onPaste={(event) => { if (event.clipboardData.files.length) { event.preventDefault(); void attachments.enqueue(Array.from(event.clipboardData.files)); } }}
      onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }} />
    {error && <p role="alert" className="px-3 py-1 text-xs text-destructive">{error}</p>}
    <div className="flex min-w-0 items-center gap-1 px-1 pb-0.5">
      <input ref={files} type="file" multiple accept={ACCEPT_ATTR} className="hidden" onChange={(event) => { void attachments.enqueue(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
      <Button variant="ghost" size="icon" aria-label={t("thread.composer.attachImage")} disabled={disabled || attachments.full} onClick={() => files.current?.click()}
        className="h-9 w-9 rounded-full text-muted-foreground hover:text-foreground"><Paperclip className="h-[1.05rem] w-[1.05rem]" strokeWidth={1.75} /></Button>
      {modelPresets?.length && onModelPresetChange ? (
        <div className="relative min-w-0">
          <select aria-label={t("settings.nav.models")}
            className="h-8 max-w-52 appearance-none truncate rounded-full border border-transparent bg-transparent pl-3 pr-7 text-[12px] text-muted-foreground outline-none transition-colors hover:border-border/60 hover:bg-background/40 hover:text-foreground focus-visible:border-border/70"
            value={modelPreset ?? ""} onChange={(event) => onModelPresetChange(event.target.value)}>
            {modelPresets.map((preset) => <option key={preset.name} value={preset.name}>{preset.name}</option>)}
          </select>
          <ChevronDown className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" aria-hidden />
        </div>
      ) : modelLabel ? (
        <button type="button" onClick={onModelBadgeClick}
          className="h-8 max-w-52 truncate rounded-full px-3 text-[12px] text-muted-foreground transition-colors hover:bg-background/40 hover:text-foreground">
          {modelLabel}
        </button>
      ) : null}
      <div className="ml-auto flex items-center gap-0.5">
        {onTranscribeAudio && <Button variant="ghost" size="icon" aria-label={t("thread.composer.tools.voice")} disabled={voice.buttonDisabled} onClick={voice.handleClick}
          className="h-9 w-9 rounded-full text-muted-foreground hover:text-foreground"><Mic className="h-[1.05rem] w-[1.05rem]" strokeWidth={1.75} /></Button>}
        {isStreaming && onStop && <Button variant="default" size="icon" aria-label={t("thread.composer.stop")} onClick={onStop}
          className="h-9 w-9 rounded-full"><Square className="h-3 w-3" fill="currentColor" /></Button>}
        <Button size="icon" aria-label={t("thread.composer.send")}
          disabled={disabled || sending || attachments.encoding || (!value.trim() && !attachments.images.length)}
          onClick={() => { void send(); }}
          className="ml-0.5 h-9 w-9 rounded-full shadow-[0_1px_2px_rgba(20,28,42,0.16)] active:scale-95 motion-reduce:active:scale-100">
          <ArrowUp className="h-[1.1rem] w-[1.1rem]" strokeWidth={2.25} />
        </Button>
      </div>
    </div>
  </div>;
}
