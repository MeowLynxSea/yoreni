import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Activity,
  Check,
  ChevronDown,
  CircleAlert,
  Clock3,
  Copy,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { AttachmentTile } from "@/components/AttachmentTile";
import { LinkCard } from "@/components/LinkCard";
import { CodeBlock } from "@/components/CodeBlock";
import { FallbackResponseSources } from "@/components/ResponseSourceBadge";
import { HtmlAttachment } from "@/components/HtmlAttachment";
import { ImageGallery } from "@/components/ImageGallery";
import { InlineImageProvider, useInlineImages } from "@/components/InlineImageContext";
import { MarkdownText } from "@/components/MarkdownText";
import { ContextCompactionNotice } from "@/components/thread/ContextCompactionNotice";
import { QuotedMessageLink } from "@/components/thread/QuotedMessageLink";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { UserMessageText } from "@/components/UserMessageText";
import { HoverHint } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { copyTextToClipboard } from "@/lib/clipboard";
import { formatMessageEndTime, formatMessageHoverTimeParts } from "@/lib/format-time";
import { toMediaAttachment } from "@/lib/media";
import { messageCopyText } from "@/lib/message-text";
import { parseQuotedUserMessage } from "@/lib/user-message-quote";
import type {
  CliAppInfo,
  McpPresetInfo,
  UICliAppAttachment,
  UIMcpPresetAttachment,
  UIImage,
  UIMediaAttachment,
  UIMessage,
  MessageDeliveryErrorKind,
  MessageDeliveryStatus,
} from "@/lib/types";

interface MessageBubbleProps {
  message: UIMessage;
  /** Give temporary-chat user turns the dashed private-mode treatment. */
  temporary?: boolean;
  cliApps?: CliAppInfo[];
  mcpPresets?: McpPresetInfo[];
  onOpenFilePreview?: (path: string) => void;
  /** Context-menu trigger positioned against this message's visual block. */
  contextMenu?: ReactNode;
  /** Scroll the transcript to the message a quote came from. */
  onJumpToQuote?: (targetId: string | null, quote: string) => void;
  /** True just after a jump landed on this message, so it can flash once. */
  quoteTarget?: boolean;
}

function ForkArrowIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <path d="M16 3h5v5" />
      <path d="M8 3H3v5" />
      <path d="m21 3-7.536 7.536A5 5 0 0 0 12 14.07V21" />
      <path d="m3 3 7.536 7.536A5 5 0 0 1 12 14.07V15" />
    </svg>
  );
}

function useMessageCopy(content: string) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copyResetRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (copyResetRef.current !== null) {
        window.clearTimeout(copyResetRef.current);
      }
    };
  }, []);

  const onCopy = useCallback(() => {
    void copyTextToClipboard(content).then((ok) => {
      if (!ok) return;
      setCopied(true);
      if (copyResetRef.current !== null) {
        window.clearTimeout(copyResetRef.current);
      }
      copyResetRef.current = window.setTimeout(() => {
        setCopied(false);
        copyResetRef.current = null;
      }, 1_500);
    });
  }, [content]);

  const label = copied ? t("message.copiedReply") : t("message.copyReply");
  return { copied, label, onCopy };
}

/** The same copy behavior in the desktop menu and the mobile reply footer. */
export function MessageCopyButton({ message, className }: { message: UIMessage; className?: string }) {
  const { t } = useTranslation();
  const content = messageCopyText(message, t);
  const { copied, label, onCopy } = useMessageCopy(content);
  if (!content.trim()) return null;
  return <HoverHint content={label} side="top" align="center"><button type="button" data-message-block-copy-action
    data-assistant-copy-action={message.role === "assistant" || undefined}
    onClick={onCopy} aria-label={label}
    className={cn(
      "inline-flex h-[var(--message-block-control-size)] w-[var(--message-block-action-width)] items-center justify-center rounded-control",
      "text-muted-foreground transition-[color] hover:text-foreground",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transform-none",
      className,
    )}>
    {copied ? <Check className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
      : <Copy className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />}
  </button></HoverHint>;
}

/** The time a reader associates with the turn: when they sent it, or when the Agent finished. */
function messageTimeAnchor(message: UIMessage): number {
  return message.role === "assistant"
    && typeof message.completedAt === "number"
    && Number.isFinite(message.completedAt)
    ? message.completedAt
    : message.createdAt;
}

interface MessageBlockMenuActivity {
  label: string;
  expanded: boolean;
  controls: string;
  onToggle: () => void;
}

interface MessageBlockMenuActionsProps {
  message: UIMessage;
  isTurnStreaming?: boolean;
  onForkFromHere?: () => void;
  activity?: MessageBlockMenuActivity;
  sheet?: boolean;
}

/** Actions and metadata shown after opening a visual message block's context panel. */
export function MessageBlockMenuActions({
  message,
  isTurnStreaming = false,
  onForkFromHere,
  activity,
  sheet = false,
}: MessageBlockMenuActionsProps) {
  const { t } = useTranslation();
  const content = messageCopyText(message, t);
  const hasText = content.trim().length > 0;
  const showFork = message.role === "assistant"
    && !message.isStreaming
    && !isTurnStreaming
    && hasText
    && onForkFromHere !== undefined;
  const timestamp = messageTimeAnchor(message);
  const timestampLabel = formatMessageEndTime(timestamp);
  const automationSourceLabel = message.role === "assistant"
    && ["cron", "local_trigger", "trigger"].includes(message.source?.kind ?? "")
    ? message.source?.label?.trim() || t("message.automationSourceFallback")
    : "";

  return (
      <div
        data-message-block-menu-actions
        className={cn("flex max-w-full flex-col items-start gap-1", sheet ? "w-full" : "w-max")}
      >
        {(!sheet && hasText) || showFork || activity ? (
          <div
            data-message-block-toolbar
            className={cn("flex w-full flex-wrap gap-x-1 gap-y-1", sheet ? "flex-col items-stretch" : "items-center")}
          >
            {(!sheet && hasText) || showFork ? (
              <div className="flex min-h-[var(--message-block-control-size)] items-center gap-0.5">
                {!sheet && hasText ? <MessageCopyButton message={message} /> : null}
                {showFork ? (
                  <HoverHint content={t("message.forkFromHere")} side="top" align="center">
                      <button
                        type="button"
                        data-message-block-fork-action
                        data-assistant-fork-action
                        onClick={onForkFromHere}
                        aria-label={t("message.forkFromHere")}
                        className={cn(
                          "inline-flex h-[var(--message-block-control-size)] w-[var(--message-block-action-width)] items-center justify-center rounded-control",
                          "text-muted-foreground transition-colors",
                          "hover:text-foreground",
                          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          "motion-reduce:transform-none",
                          sheet && "w-full justify-start gap-2 px-2 text-sm",
                        )}
                      >
                        <ForkArrowIcon className="h-3.5 w-3.5" />
                        {sheet ? <span>{t("message.forkFromHere")}</span> : null}
                      </button>
                  </HoverHint>
                ) : null}
              </div>
            ) : null}
            {activity ? (
              <button
                type="button"
                data-message-block-activity-action
                onClick={activity.onToggle}
                aria-expanded={activity.expanded}
                aria-controls={activity.controls}
                className={cn(
                  "group inline-flex min-h-[var(--message-block-control-size)] self-start items-center gap-1.5 whitespace-nowrap",
                  "text-start text-[11px] leading-4",
                  "text-muted-foreground transition-colors hover:text-foreground",
                  "focus-visible:outline-none",
                  sheet && "w-full gap-2 rounded-control px-2 text-sm hover:bg-muted/70",
                )}
              >
                <span
                  data-message-block-activity-icon
                  className={cn(
                    "inline-flex h-[var(--message-block-control-size)] w-[var(--message-block-action-width)] shrink-0 items-center justify-center rounded-control",
                    "transition-colors",
                    "group-focus-visible:ring-2 group-focus-visible:ring-ring",
                    "motion-reduce:transform-none",
                    sheet && "w-3.5",
                  )}
                >
                  <Activity className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
                </span>
                <span>{activity.label}</span>
              </button>
            ) : null}
          </div>
        ) : null}
        <FallbackResponseSources
          sources={message.role === "assistant" ? message.responseSources : undefined}
          className="min-h-[var(--message-block-control-size)] gap-1"
          badgeClassName="h-[var(--message-block-control-size)] min-h-[var(--message-block-control-size)]"
        />
        {timestampLabel || automationSourceLabel ? (
          <div
            data-message-block-metadata
            className={cn("mt-0.5 w-full border-t border-border/45 px-1.5 pt-1 leading-4 text-muted-foreground", sheet ? "px-2 text-xs" : "text-[10px]")}
          >
            {timestampLabel ? (
              <time
                data-message-timestamp
                data-message-created-at={message.role === "user" || undefined}
                data-assistant-completed-at={
                  message.role === "assistant" && timestamp === message.completedAt || undefined
                }
                dateTime={new Date(timestamp).toISOString()}
                className="flex min-h-[var(--message-block-control-size)] items-center tabular-nums"
              >
                {timestampLabel}
              </time>
            ) : null}
            {automationSourceLabel ? (
              <span
                data-automation-trigger
                className="flex min-h-[var(--message-block-control-size)] flex-wrap items-baseline gap-x-2 break-words"
              >
                <span>{t("message.automationTriggered")}</span>{" "}
                <span>{automationSourceLabel}</span>
              </span>
            ) : null}
          </div>
        ) : null}
      </div>
  );
}

function deliveryErrorCopy(
  kind: MessageDeliveryErrorKind | undefined,
  t: (key: string) => string,
): { title: string; body: string } {
  switch (kind) {
    case "message_too_big":
      return {
        title: t("errors.messageTooBig.title"),
        body: t("errors.messageTooBig.body"),
      };
    case "workspace_scope_rejected":
      return {
        title: t("errors.workspaceScopeRejected.title"),
        body: t("errors.workspaceScopeRejected.body"),
      };
    case "turn_rejected":
    case undefined:
      return {
        title: t("errors.turnRejected.title"),
        body: t("errors.turnRejected.body"),
      };
    default: {
      const _exhaustive: never = kind;
      return { title: String(_exhaustive), body: "" };
    }
  }
}

function UserDeliveryStatus({
  status,
  errorKind,
}: {
  status: MessageDeliveryStatus | undefined;
  errorKind: MessageDeliveryErrorKind | undefined;
}) {
  const { t } = useTranslation();
  if (status !== "sending" && status !== "failed") return null;
  if (status === "sending") {
    return (
      <span
        role="status"
        className="inline-flex items-center gap-1 text-[12px] leading-none text-muted-foreground"
      >
        <Clock3 className="h-3.5 w-3.5" aria-hidden />
        {t("message.delivery.sending")}
      </span>
    );
  }

  const label = t("message.delivery.failed");
  const { title, body } = deliveryErrorCopy(errorKind, t);
  return (
    <>
      <HoverHint
        side="top"
        align="end"
        contentClassName="max-w-72 px-3 py-2.5 text-left"
        content={<>
          <p className="font-medium text-popover-foreground">{title}</p>
          <p className="mt-1 leading-relaxed text-muted-foreground">{body}</p>
        </>}
      >
          <button
            type="button"
            aria-label={`${label}: ${title}`}
            className={cn(
              "inline-flex items-center gap-1 rounded-sm text-[12px] leading-none",
              "text-destructive/80 transition-colors hover:text-destructive",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              "dark:text-red-400/80 dark:hover:text-red-400",
            )}
          >
            <CircleAlert className="h-3.5 w-3.5" aria-hidden />
            {label}
          </button>
      </HoverHint>
      <span role="alert" aria-live="assertive" className="sr-only">
        {title}. {body}
      </span>
    </>
  );
}

/** Answers longer than this read as walls of text, so the bubble trims them. */
const LONG_MESSAGE_CHARS = 1_200;

/** Cut a long answer at a natural boundary so the bubble ends on a whole thought. */
function trimmedMessage(content: string): string {
  const window = content.slice(0, LONG_MESSAGE_CHARS);
  const boundary = Math.max(
    window.lastIndexOf("\n\n"),
    window.lastIndexOf("\n"),
    window.lastIndexOf("。"),
    window.lastIndexOf("！"),
    window.lastIndexOf("？"),
    window.lastIndexOf(". "),
  );
  const end = boundary > LONG_MESSAGE_CHARS * 0.6 ? boundary + 1 : LONG_MESSAGE_CHARS;
  return content.slice(0, end).trimEnd();
}

/**
 * The send time, revealed above the message on hover: the Agent's on the left,
 * the reader's on the right, stacked date over clock like a chat client. It is
 * anchored to the message's own edges, so a narrow window never widens the
 * transcript to make room for the label. The label refreshes when the message is
 * entered again so an overnight session does not keep calling yesterday "today".
 */
function MessageHoverTime({ timestamp, align }: { timestamp: number; align: "start" | "end" }) {
  const [now, setNow] = useState(() => new Date());
  const ref = useRef<HTMLTimeElement>(null);
  useEffect(() => {
    const element = ref.current;
    const owner = element?.closest("[data-thread-display-unit]") ?? element?.closest("[data-message-id]");
    if (!owner) return;
    const refresh = () => setNow(new Date());
    owner.addEventListener("pointerenter", refresh);
    return () => owner.removeEventListener("pointerenter", refresh);
  }, []);
  const parts = formatMessageHoverTimeParts(timestamp, undefined, now);
  if (!parts) return null;
  return (
    <HoverHint side="top" align={align === "start" ? "start" : "end"} content={parts.full}>
      <time
        ref={ref}
        data-message-hover-time
        dateTime={new Date(timestamp).toISOString()}
        aria-label={parts.full}
        tabIndex={0}
        onFocus={() => setNow(new Date())}
        className={cn(
          "pointer-events-none absolute bottom-full z-10 mb-1 hidden select-none flex-col",
          "whitespace-nowrap text-[10px] leading-[1.45] tabular-nums text-muted-foreground/60",
          "opacity-0 transition-opacity duration-150 motion-reduce:transition-none",
          "focus-visible:opacity-100 focus-visible:outline-none group-hover/message:opacity-100",
          "group-focus-within/message:opacity-100 md:flex",
          align === "start" ? "start-0 items-start text-start" : "end-0 items-end text-end",
        )}
      >
        {parts.date ? <span>{parts.date}</span> : null}
        <span>{parts.time}</span>
      </time>
    </HoverHint>
  );
}

/** Reactions sit on the bubble's bottom corner, the way a chat app shows them. */
function MessageReactions({
  reactions,
  side,
}: {
  reactions: string[] | undefined;
  side: "left" | "right";
}) {
  const { t } = useTranslation();
  if (!reactions?.length) return null;
  return (
    <span
      data-message-reactions
      className={cn(
        "absolute -bottom-4 z-10 flex items-center gap-1 rounded-full",
        "border border-border/70 bg-background/95 px-2 py-1 shadow-sm backdrop-blur",
        "animate-in fade-in-0 zoom-in-50 duration-200 ease-out motion-reduce:animate-none",
        side === "right" ? "right-3 origin-bottom-right" : "left-4 origin-bottom-left",
      )}
    >
      {reactions.map((emoji) => (
        <span
          key={emoji}
          role="img"
          aria-label={t("message.reactionFromAgent")}
          className="select-none text-[18px] leading-none animate-in zoom-in-50 duration-150 motion-reduce:animate-none"
        >
          {emoji}
        </span>
      ))}
    </span>
  );
}

function DeliveredMessageBody({
  content,
  streaming,
  onOpenFilePreview,
}: {
  content: string;
  streaming: boolean;
  onOpenFilePreview?: (path: string) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const long = !streaming && content.length > LONG_MESSAGE_CHARS;
  if (!long) {
    return (
      <MarkdownText
        streaming={streaming}
        preserveStreamingLayout
        onOpenFilePreview={onOpenFilePreview}
      >
        {content}
      </MarkdownText>
    );
  }
  const label = t("message.readFullMessage");
  return (
    <>
      <div
        data-message-trimmed
        className={cn(
          "relative max-h-[26rem] overflow-hidden",
          "[mask-image:linear-gradient(to_bottom,black_72%,transparent)]",
        )}
      >
        <MarkdownText onOpenFilePreview={onOpenFilePreview}>{trimmedMessage(content)}</MarkdownText>
        <button
          type="button"
          onClick={() => setOpen(true)}
          tabIndex={-1}
          aria-hidden
          className="absolute inset-0 cursor-pointer"
        />
      </div>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn(
          "mt-2 inline-flex items-center gap-1 rounded-control text-[13px] font-medium",
          "text-muted-foreground transition-colors hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        {label}
        <ChevronDown className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          aria-describedby={undefined}
          className="max-h-[85dvh] w-[min(92vw,46rem)] overflow-y-auto overscroll-contain"
        >
          <DialogTitle>{t("message.fullMessageTitle")}</DialogTitle>
          <div data-assistant-selectable="true" className="text-reading">
            <MarkdownText onOpenFilePreview={onOpenFilePreview}>{content}</MarkdownText>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

/** Render user turns as compact bubbles and assistant turns as document-like prose. */
export function MessageBubble({
  message,
  temporary = false,
  cliApps = [],
  mcpPresets = [],
  onOpenFilePreview,
  contextMenu,
  onJumpToQuote,
  quoteTarget = false,
}: MessageBubbleProps) {
  const { t } = useTranslation();
  const mentionCliApps = useMemo(
    () => mergeCliMentionApps(cliApps, message.cliApps),
    [cliApps, message.cliApps],
  );
  const mentionMcpPresets = useMemo(
    () => mergeMcpMentionPresets(mcpPresets, message.mcpPresets),
    [mcpPresets, message.mcpPresets],
  );

  if (message.kind === "compaction" && message.compaction) {
    return <ContextCompactionNotice compaction={message.compaction} />;
  }

  // Tool calls and their progress are runtime detail, not conversation; the
  // transcript never renders them.
  if (message.kind === "trace") return null;

  if (message.role === "user") {
    const images = message.images ?? [];
    const media = message.media ?? [];
    const hasImages = images.length > 0;
    const hasMedia = media.length > 0;
    const parsedMessage = parseQuotedUserMessage(message.content);
    const userContent = parsedMessage.content;
    const hasText = userContent.trim().length > 0;
    const showDeliveryStatus =
      message.deliveryStatus === "sending" || message.deliveryStatus === "failed";
    const quotedContext = parsedMessage.quotedContext;
    const messageText = (
      <UserMessageText
        text={userContent}
        cliApps={mentionCliApps}
        mcpPresets={mentionMcpPresets}
      />
    );
    return (
      <div
        data-message-id={message.id}
        className="message-enter group/message relative ml-auto flex w-fit max-w-[min(85%,36rem)] flex-col items-end gap-1.5"
      >
        <MessageHoverTime timestamp={message.createdAt} align="end" />
        {quotedContext ? (
          <QuotedMessageLink
            quote={quotedContext}
            targetId={parsedMessage.quotedMessageId}
            onJump={onJumpToQuote}
            align="end"
            className="me-1 -mb-0.5"
          />
        ) : null}
        {!hasText ? contextMenu : null}
        {hasImages ? <ImageGallery images={images} align="right" size="compact" /> : null}
        {!hasImages && hasMedia ? (
          <MessageMedia media={media} align="right" pending={message.deliveryStatus === "sending"} />
        ) : null}
        {!hasText ? <MessageReactions reactions={message.reactions} side="right" /> : null}
        {hasText ? (
          <div data-user-text-bubble className="relative ml-auto w-fit max-w-full min-w-0">
            {contextMenu}
            <p
              data-theme-surface
              data-message-selectable="true"
              data-quote-target={quoteTarget || undefined}
              data-temporary-message={temporary ? "true" : undefined}
              className={cn(
                "ml-auto w-fit max-w-full min-w-0 rounded-[1.35rem] px-4 py-2.5",
                "text-left text-reading whitespace-pre-wrap [overflow-wrap:anywhere]",
                temporary
                  ? "border border-dashed border-muted-foreground/40 bg-transparent"
                  : "user-message-bubble",
              )}
            >
              {messageText}
            </p>
            <MessageReactions reactions={message.reactions} side="right" />
          </div>
        ) : null}
        {showDeliveryStatus ? (
            <div className="flex min-h-8 items-center justify-end gap-1.5 text-muted-foreground">
              <UserDeliveryStatus
                status={message.deliveryStatus}
                errorKind={message.deliveryErrorKind}
              />
            </div>
        ) : null}
      </div>
    );
  }

  const assistantContent = messageCopyText(message, t);
  const media = message.media ?? [];
  if (assistantContent.trim().length === 0 && media.length === 0) {
    // A pre-token or tool-only slot has no answer of its own; the run indicator
    // carries the waiting state instead of an empty sheet that flashes away.
    return null;
  }
  if (message.rich) {
    // Files are their own card: a speech bubble around them would read as
    // something the Agent said rather than something it handed over.
    return (
      <div
        data-assistant-message
        data-message-id={message.id}
        className="message-enter group/message relative w-full text-reading"
      >
        <MessageHoverTime timestamp={messageTimeAnchor(message)} align="start" />
        {contextMenu}
        {message.quote ? (
          <div className="mb-1 ms-1 flex">
            <QuotedMessageLink
              quote={message.quote}
              targetId={message.replyTo ?? null}
              onJump={onJumpToQuote}
            />
          </div>
        ) : null}
        <InlineImageProvider>
          <figure
            data-rich-card
            data-quote-target={quoteTarget || undefined}
            className="flex w-full max-w-[28rem] flex-col gap-2"
          >
            {media.length > 0 ? <MessageMedia media={media} align="left" fill /> : null}
            {assistantContent.trim().length > 0 ? (
              <figcaption className="whitespace-pre-wrap text-caption text-muted-foreground">
                {assistantContent}
              </figcaption>
            ) : null}
          </figure>
        </InlineImageProvider>
        <MessageReactions reactions={message.reactions} side="left" />
      </div>
    );
  }
  return (
    <div
      data-assistant-message
      data-message-id={message.id}
      className={cn(
        "message-enter group/message relative w-full text-reading",
        message.isStreaming && "assistant-caret",
      )}
    >
      <MessageHoverTime timestamp={messageTimeAnchor(message)} align="start" />
      {contextMenu}
      {message.quote ? (
        <div className="mb-1 ms-1 flex min-w-0">
          <QuotedMessageLink
            quote={message.quote}
            targetId={message.replyTo ?? null}
            onJump={onJumpToQuote}
          />
        </div>
      ) : null}
      <div
        data-quote-target={quoteTarget || undefined}
        className="app-surface-glass rounded-[1.6rem] px-5 py-4 sm:px-6 sm:py-5"
      >
        <InlineImageProvider>
          <div
            data-message-selectable={message.isStreaming ? undefined : "true"}
            data-assistant-selectable={message.isStreaming ? undefined : "true"}
          >
            {message.renderAs === "text" ? (
              <CodeBlock language="text" code={assistantContent} highlight={false} ansi={false} />
            ) : message.delivered ? (
              <DeliveredMessageBody
                content={assistantContent}
                streaming={!!message.isStreaming}
                onOpenFilePreview={onOpenFilePreview}
              />
            ) : (
              /* A mode switch rebuilds Streamdown's subtree and moves the scroll anchor. */
              <MarkdownText
                streaming={!!message.isStreaming}
                preserveStreamingLayout
                onOpenFilePreview={onOpenFilePreview}
              >
                {assistantContent}
              </MarkdownText>
            )}
          </div>
          {media.length > 0 ? <MessageMedia media={media} align="left" /> : null}
        </InlineImageProvider>
      </div>
      <MessageReactions reactions={message.reactions} side="left" />
    </div>
  );
}

function mergeMcpMentionPresets(
  presets: McpPresetInfo[],
  attachments: UIMcpPresetAttachment[] | undefined,
): McpPresetInfo[] {
  if (!attachments?.length) return presets;
  const byName = new Map(presets.map((preset) => [preset.name.toLowerCase(), preset]));
  for (const attachment of attachments) {
    const name = attachment.name?.trim();
    if (!name) continue;
    const existing = byName.get(name.toLowerCase());
    byName.set(name.toLowerCase(), {
      name,
      display_name: attachment.display_name || existing?.display_name || name,
      category: attachment.category || existing?.category || "mcp",
      description: existing?.description || "",
      docs_url: existing?.docs_url || "",
      transport: attachment.transport || existing?.transport || "mcp",
      requires: existing?.requires || "",
      note: existing?.note || "",
      install_supported: existing?.install_supported ?? true,
      installed: true,
      configured: attachment.configured ?? existing?.configured ?? true,
      available: existing?.available ?? true,
      status: attachment.status || existing?.status || "configured",
      logo_url: attachment.logo_url ?? existing?.logo_url ?? null,
      brand_color: attachment.brand_color ?? existing?.brand_color ?? null,
      required_fields: existing?.required_fields || [],
      connection_summary: existing?.connection_summary || "",
    });
  }
  return Array.from(byName.values());
}

function mergeCliMentionApps(
  cliApps: CliAppInfo[],
  attachments: UICliAppAttachment[] | undefined,
): CliAppInfo[] {
  if (!attachments?.length) return cliApps;
  const byName = new Map(cliApps.map((app) => [app.name.toLowerCase(), app]));
  for (const attachment of attachments) {
    const name = attachment.name?.trim();
    if (!name) continue;
    const existing = byName.get(name.toLowerCase());
    byName.set(name.toLowerCase(), {
      name,
      display_name: attachment.display_name || existing?.display_name || name,
      category: attachment.category || existing?.category || "cli",
      description: existing?.description || "",
      requires: existing?.requires || "",
      source: existing?.source || "attached",
      entry_point: attachment.entry_point || existing?.entry_point || "",
      install_supported: existing?.install_supported ?? true,
      installed: true,
      available: existing?.available ?? true,
      status: existing?.status || "installed",
      logo_url: attachment.logo_url ?? existing?.logo_url ?? null,
      brand_color: attachment.brand_color ?? existing?.brand_color ?? null,
      skill_installed: existing?.skill_installed ?? true,
    });
  }
  return Array.from(byName.values());
}

function MessageMedia({
  media,
  align,
  pending = false,
  fill = false,
}: {
  media: UIMediaAttachment[];
  align: "left" | "right";
  pending?: boolean;
  /** Reach the container edge: no outer margin, previews cropped to their frame. */
  fill?: boolean;
}) {
  const inlineImages = useInlineImages();
  if (media.length === 0) return null;
  const images: UIImage[] = [];
  const seen = new Set<string>();
  const links: UIMediaAttachment[] = [];
  const videos: UIMediaAttachment[] = [];
  const pages: UIMediaAttachment[] = [];
  const files: UIMediaAttachment[] = [];
  for (const item of media) {
    const normalized = toMediaAttachment(item);
    if (normalized.kind === "link") {
      links.push(normalized);
    } else if (normalized.kind === "image") {
      if (normalized.url && (inlineImages.has(normalized.url) || seen.has(normalized.url))) continue;
      if (normalized.url) seen.add(normalized.url);
      images.push({ url: normalized.url, name: normalized.name });
    } else if (normalized.kind === "video") {
      videos.push(normalized);
    } else if (normalized.kind === "html") {
      pages.push(normalized);
    } else {
      files.push(normalized);
    }
  }
  if (
    links.length === 0
    && images.length === 0
    && videos.length === 0
    && pages.length === 0
    && files.length === 0
  ) {
    return null;
  }

  return (
    <div
      className={cn(
        "flex min-w-0 max-w-full flex-col gap-2",
        !fill && "mt-2",
        align === "right" ? "items-end" : "items-start",
      )}
    >
      {links.map((item, i) => <LinkCard key={`link-${i}`} link={item} />)}
      {images.length > 0 ? (
        <ImageGallery images={images} align={align} size={align === "left" ? "large" : "compact"} fill={fill} />
      ) : null}
      {pages.map((item, i) => (
        <HtmlAttachment key={`html-${i}`} attachment={item} className="my-0 w-[min(100%,32rem)]" />
      ))}
      {videos.map((item, i) => (
        <AttachmentTile key={`video-${i}`} attachment={item} pending={pending} className="my-0" />
      ))}
      {files.length > 0 ? (
        <div className={cn("flex w-full min-w-0 flex-wrap items-start gap-2", align === "right" && "justify-end")}>
          {files.map((item, i) => (
            <AttachmentTile key={`file-${i}`} attachment={item} pending={pending} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
