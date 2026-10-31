import { useCallback, useEffect, useRef, useState, type Ref } from "react";
import { ArrowUp, ChevronDown, ImageIcon, Loader2, Mic, Paperclip, Quote, Square, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { EmojiText } from "@/components/EmojiText";
import { FileReferenceIcon, fileKindForPath } from "@/components/FileReferenceChip";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useAttachedImages, MAX_VIDEO_BYTES, type AttachedAttachment, type AttachmentLimits } from "@/hooks/useAttachedImages";
import { useVoiceRecorder } from "@/hooks/useVoiceRecorder";
import { formatFileSize } from "@/lib/format";
import { readComposerDraft, writeComposerDraft } from "@/lib/local-ui-state";
import { cn } from "@/lib/utils";
import type { SendAttachment, SendOptions } from "@/lib/agent-input";
import type { LocalAttachment } from "../../../../packages/client-events/attachments";
import type { WebUIIngressLimits } from "@/lib/types";
import type { ModelPresetOption } from "./ModelPresetBadge";

/** Matches the quote strip's collapse transition so it unmounts after the last frame. */
const QUOTE_STRIP_EXIT_MS = 200;

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
  /** Text the next message will quote; shown above the input until it is sent or dropped. */
  quotedContext?: string | null;
  onClearQuote?: () => void;
  /** Restore and keep the unsent text across reloads and route changes. */
  persistDraft?: boolean;
}

/** The composer owns its text and attachments; execution belongs to the Agent. */
export function ThreadComposer({ onSend, onStop, disabled = false, isStreaming = false, placeholder, inputAriaLabel, variant = "thread", onTranscribeAudio, ingressLimits, surfaceRef, modelLabel, modelPreset, modelPresets, onModelPresetChange, onModelBadgeClick, persistDraft = false, quotedContext, onClearQuote }: Props) {
  const { t } = useTranslation();
  const [value, setValue] = useState(() => (persistDraft ? readComposerDraft() : ""));
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const files = useRef<HTMLInputElement>(null);
  const attachments = useAttachedImages({ ingressLimits });
  useEffect(() => {
    if (persistDraft) writeComposerDraft(value);
  }, [persistDraft, value]);
  // The quote strip grows and shrinks with the composer instead of snapping the
  // layout. It stays mounted through the collapse so the text keeps its shape.
  const [quoteMounted, setQuoteMounted] = useState(quotedContext != null);
  const [quoteExpanded, setQuoteExpanded] = useState(false);
  const [quotePreview, setQuotePreview] = useState(quotedContext ?? "");
  // Quoting is a prelude to typing, so the caret belongs in the input already.
  useEffect(() => {
    if (!quotedContext) return;
    setQuotePreview(quotedContext);
    setQuoteMounted(true);
    // Paint the collapsed row once so the next frame can animate it open.
    const frame = requestAnimationFrame(() => setQuoteExpanded(true));
    inputRef.current?.focus({ preventScroll: true });
    return () => cancelAnimationFrame(frame);
  }, [quotedContext]);
  useEffect(() => {
    if (quotedContext) return;
    setQuoteExpanded(false);
    const timer = window.setTimeout(() => setQuoteMounted(false), QUOTE_STRIP_EXIT_MS);
    return () => window.clearTimeout(timer);
  }, [quotedContext]);
  const appendTranscript = useCallback((text: string) => setValue((current) => current ? `${current}\n${text}` : text), []);
  const voice = useVoiceRecorder({ disabled, onClearError: () => setError(null), onError: (key) => setError(t(`thread.composer.voice.errors.${key}`)), onTranscript: appendTranscript, onTranscribeAudio });
  // A rejected or still-encoding attachment cannot be delivered, and sending
  // the text alone would silently drop it.
  const attachmentsReady = attachments.images.every((image) => image.status === "ready");
  const send = async () => {
    if (sending || disabled || !attachmentsReady || (!value.trim() && !attachments.images.length)) return;
    const content = value;
    const media: SendAttachment[] = attachments.images.map((image) => ({
      media: image.local
        ? { path: image.local.path, name: image.file.name }
        : { data_url: image.dataUrl!, name: image.file.name },
      // The bubble outlives the composer's staged blob URLs, so an image keeps
      // its own encoded copy as the preview until the gateway signs the file.
      preview: {
        kind: image.kind,
        url: image.local?.url || (image.kind === "image" ? image.dataUrl : undefined),
        name: image.file.name,
        size: image.file.size,
      },
    }));
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
    {attachments.images.length > 0 && <div data-composer-attachments className="mb-1 flex flex-wrap gap-2 px-1 pt-1">{attachments.images.map((image) => <ComposerAttachment key={image.id} attachment={image} limits={attachments.limits}
      onRemove={() => { attachments.remove(image.id); inputRef.current?.focus({ preventScroll: true }); }} />)}</div>}
    {quoteMounted ? <div data-composer-quote data-state={quoteExpanded ? "open" : "closed"}
      aria-hidden={quoteExpanded ? undefined : true}
      className={cn(
        "grid text-[12px] leading-5 transition-[grid-template-rows,opacity] duration-200 ease-out motion-reduce:transition-none",
        quoteExpanded ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0",
      )}>
      <div className="min-h-0 overflow-hidden">
        <div className="mx-1 mb-1 flex items-start gap-2 rounded-control border border-border/60 bg-muted/35 px-2.5 py-1.5 text-muted-foreground">
          <Quote className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} aria-hidden />
          <p className="line-clamp-2 min-w-0 flex-1 whitespace-pre-wrap [overflow-wrap:anywhere]"><EmojiText text={quotePreview} /></p>
          <button type="button" tabIndex={quoteExpanded ? undefined : -1}
            onClick={() => { inputRef.current?.focus({ preventScroll: true }); onClearQuote?.(); }}
            aria-label={t("thread.composer.removeQuotedContext")}
            className="-me-1 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <X className="h-3 w-3" strokeWidth={2} aria-hidden />
          </button>
        </div>
      </div>
    </div> : null}
    <Textarea ref={inputRef} value={value} disabled={disabled} aria-label={inputAriaLabel ?? t("thread.composer.inputAria")}
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
      <input ref={files} type="file" multiple className="hidden" onChange={(event) => { void attachments.enqueue(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
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
          disabled={disabled || sending || !attachmentsReady || (!value.trim() && !attachments.images.length)}
          onClick={() => { void send(); }}
          className="ml-0.5 h-9 w-9 rounded-full shadow-[0_1px_2px_rgba(20,28,42,0.16)] active:scale-95 motion-reduce:active:scale-100">
          <ArrowUp className="h-[1.1rem] w-[1.1rem]" strokeWidth={2.25} />
        </Button>
      </div>
    </div>
  </div>;
}

/** A staged attachment stays recognizable before it is sent: an image is its
 * own thumbnail, a file its name and size, and a failure says why it cannot
 * go out. Dismissing one is always the same corner control. */
function ComposerAttachment({ attachment, limits, onRemove }: {
  attachment: AttachedAttachment;
  limits: AttachmentLimits;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const name = attachment.file.name;
  const failed = attachment.status === "error";
  const encoding = attachment.status === "encoding";
  const remove = <button type="button" onClick={onRemove} aria-label={t("thread.composer.remove")}
    className="absolute right-1 top-1 grid size-5 place-items-center rounded-full bg-background/85 text-foreground/65 shadow-[0_1px_2px_rgba(0,0,0,0.22)] backdrop-blur transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
    <X className="size-3" strokeWidth={2.25} aria-hidden />
  </button>;

  if (attachment.kind === "image" && !failed) {
    const preview = attachment.previewUrl || undefined;
    return <span data-composer-attachment="image" role="group" aria-label={name} className="relative shrink-0">
      <span aria-hidden className="relative grid size-12 place-items-center overflow-hidden rounded-control border border-border/60 bg-foreground/[0.04] text-muted-foreground">
        {preview ? <img src={preview} alt="" draggable={false} className="size-full object-cover" />
          : <ImageIcon className="size-4" strokeWidth={1.75} />}
        {encoding ? <span className="absolute inset-0 grid place-items-center bg-background/70">
          <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" />
        </span> : null}
      </span>
      {remove}
    </span>;
  }

  const detail = failed && attachment.error
    ? rejectionCopy(attachment, limits, t)
    : encoding
      ? t("thread.composer.encoding")
      : attachment.normalized && attachment.encodedBytes
        ? t("thread.composer.normalizedSizeHint", {
            orig: formatFileSize(attachment.file.size),
            current: formatFileSize(attachment.encodedBytes),
          })
        : formatFileSize(attachment.file.size);
  return <span data-composer-attachment={attachment.kind}
    className={cn(
      "relative flex h-12 max-w-full min-w-0 items-center gap-2 rounded-control border pe-7 ps-1.5",
      failed ? "border-destructive/40 bg-destructive/5" : "border-border/60 bg-background/55",
    )}>
    <span aria-hidden className="grid size-9 shrink-0 place-items-center rounded-[0.55rem] bg-foreground/[0.05] text-muted-foreground">
      {encoding ? <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
        : <FileReferenceIcon kind={fileKindForPath(name)} className="text-[17px]" />}
    </span>
    <span className="flex min-w-0 flex-col">
      {attachment.kind !== "image" ? (
        <span title={name} className="max-w-56 truncate text-[12px] font-medium leading-4 text-foreground">{shortAttachmentName(name)}</span>
      ) : null}
      <span title={detail} className={cn("max-w-56 truncate text-[11px] leading-4", failed ? "text-destructive" : "text-muted-foreground")}>{detail}</span>
    </span>
    {remove}
  </span>;
}

/** Long names lose their middle, so the extension and the beginning survive. */
function shortAttachmentName(name: string, max = 24): string {
  if (name.length <= max) return name;
  const extension = name.match(/\.(?:tar\.(?:gz|bz2|xz)|[a-z0-9]{1,12})$/i)?.[0] ?? "";
  return `${name.slice(0, Math.max(1, max - extension.length - 1))}…${extension}`;
}

function rejectionCopy(attachment: AttachedAttachment, limits: AttachmentLimits, t: ReturnType<typeof useTranslation>["t"]): string {
  const tooLarge = attachment.error === "too_large";
  return t(`thread.composer.imageRejected.${attachment.error}`, {
    max: tooLarge
      ? formatFileSize(attachment.kind === "video" ? MAX_VIDEO_BYTES : limits.fileBytes)
      : limits.count,
    size: formatFileSize(attachment.file.size),
  });
}
