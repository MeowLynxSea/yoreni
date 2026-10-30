import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Check, Copy, Quote } from "lucide-react";
import { useTranslation } from "react-i18next";

import { floatingSurfaceElevationClassName } from "@/components/ui/floating-surface";
import { copyTextToClipboard } from "@/lib/clipboard";
import { cn } from "@/lib/utils";

const MAX_QUOTED_CONTEXT_CHARS = 4_000;

export interface MessageQuoteSelection {
  /** The message the passage or whole text came from, when it could be resolved. */
  messageId: string | null;
  text: string;
}

interface MessageActionLayerProps {
  /** The transcript that owns the messages; clicks outside it are ignored. */
  containerRef: RefObject<HTMLElement | null>;
  /** Whole-message text for a context-menu action. */
  messageText: (messageId: string) => string | null;
  onQuote: (selection: MessageQuoteSelection) => void;
}

interface MenuState {
  text: string;
  messageId: string | null;
  /** Viewport point the menu is anchored to. */
  left: number;
  top: number;
  /** Grow upward from the anchor instead of downward. */
  above: boolean;
  source: "selection" | "message";
}

function messageOwner(node: Node | null, container: HTMLElement): HTMLElement | null {
  const element = node instanceof Element ? node : node?.parentElement;
  const message = element?.closest<HTMLElement>("[data-message-id]") ?? null;
  return message && container.contains(message) ? message : null;
}

function selectableOwner(node: Node | null, container: HTMLElement): HTMLElement | null {
  const element = node instanceof Element ? node : node?.parentElement;
  const selectable = element?.closest<HTMLElement>("[data-message-selectable='true']") ?? null;
  return selectable && container.contains(selectable) ? selectable : null;
}

function selectedRangeRect(range: Range): DOMRect | null {
  const rect = range.getBoundingClientRect();
  if (rect.width > 0 || rect.height > 0) return rect;
  return range.getClientRects()[0] ?? null;
}

function normalizedSelectionText(selection: Selection): string {
  return selection
    .toString()
    .replace(/\u00a0/g, " ")
    .replace(/\r\n?/g, "\n")
    .trim()
    .slice(0, MAX_QUOTED_CONTEXT_CHARS);
}

/**
 * Copy and quote actions for the transcript. Selecting text inside a message
 * offers them at the selection; right-clicking a message offers them at the
 * pointer, the way a chat client does.
 */
export function MessageActionLayer({ containerRef, messageText, onQuote }: MessageActionLayerProps) {
  const { t } = useTranslation();
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const frameRef = useRef<number | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const anchorRef = useRef<HTMLDivElement>(null);
  const firstActionRef = useRef<HTMLButtonElement>(null);
  const copiedTimerRef = useRef<number | null>(null);
  const exitTimerRef = useRef<number | null>(null);
  /** What opened the current menu; the context menu owns it until it closes. */
  const sourceRef = useRef<MenuState["source"] | null>(null);
  /** Pointer-opened menus take focus once; a drag-selection must not steal it. */
  const focusRequestRef = useRef(false);

  const openMenu = useCallback((state: MenuState) => {
    if (exitTimerRef.current !== null) {
      window.clearTimeout(exitTimerRef.current);
      exitTimerRef.current = null;
    }
    sourceRef.current = state.source;
    focusRequestRef.current = state.source === "message";
    setMenu(state);
    setOpen(true);
  }, []);

  // The menu fades out where it stood instead of blinking away mid-click.
  const close = useCallback(() => {
    setCopied(false);
    setOpen(false);
    if (exitTimerRef.current !== null) window.clearTimeout(exitTimerRef.current);
    exitTimerRef.current = window.setTimeout(() => {
      exitTimerRef.current = null;
      sourceRef.current = null;
      setMenu(null);
    }, 120);
  }, []);

  useLayoutEffect(() => {
    const anchor = anchorRef.current;
    const element = menuRef.current;
    if (!menu || !anchor || !element) return;
    anchor.style.translate = "0px 0px";
    const viewport = window.visualViewport;
    const viewportLeft = viewport?.offsetLeft ?? 0;
    const viewportTop = viewport?.offsetTop ?? 0;
    const viewportRight = viewportLeft + (viewport?.width ?? window.innerWidth);
    const viewportBottom = viewportTop + (viewport?.height ?? window.innerHeight);
    const rect = element.getBoundingClientRect();
    const padding = 12;
    const shiftX = rect.left < viewportLeft + padding
      ? viewportLeft + padding - rect.left
      : rect.right > viewportRight - padding
        ? viewportRight - padding - rect.right
        : 0;
    const shiftY = rect.top < viewportTop + padding
      ? viewportTop + padding - rect.top
      : rect.bottom > viewportBottom - padding
        ? viewportBottom - padding - rect.bottom
        : 0;
    anchor.style.translate = `${shiftX}px ${shiftY}px`;
  }, [menu]);

  useEffect(() => {
    if (!focusRequestRef.current) return;
    focusRequestRef.current = false;
    firstActionRef.current?.focus({ preventScroll: true });
  }, [menu]);

  useEffect(() => {
    const updateFromSelection = () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = requestAnimationFrame(() => {
        frameRef.current = null;
        // A context menu focuses its first action, and that focus can clear the
        // selection; those changes are not the reader dismissing the menu, and
        // treating them as such made the menu flicker open and shut. A menu that
        // is already closing must not be revived by the same click either.
        if (sourceRef.current === "message" || exitTimerRef.current !== null) return;
        const host = containerRef.current;
        const selection = window.getSelection();
        if (!host || !selection || selection.isCollapsed || selection.rangeCount === 0) {
          close();
          return;
        }
        const range = selection.getRangeAt(0);
        if (!selectableOwner(range.startContainer, host) || !selectableOwner(range.endContainer, host)) {
          close();
          return;
        }
        if (!host.contains(range.commonAncestorContainer)) {
          close();
          return;
        }
        const text = normalizedSelectionText(selection);
        const rect = selectedRangeRect(range);
        if (!text || !rect) {
          close();
          return;
        }
        const viewport = window.visualViewport;
        const viewportTop = viewport?.offsetTop ?? 0;
        const viewportBottom = viewportTop + (viewport?.height ?? window.innerHeight);
        openMenu({
          text,
          messageId: messageOwner(range.commonAncestorContainer, host)?.dataset.messageId ?? null,
          left: rect.left + rect.width / 2,
          top: rect.bottom + 8,
          above: rect.bottom + 60 > viewportBottom,
          source: "selection",
        });
      });
    };

    const onContextMenu = (event: MouseEvent) => {
      const host = containerRef.current;
      const target = event.target;
      if (!host || !(target instanceof Element) || !host.contains(target)) return;
      const owner = target.closest<HTMLElement>("[data-message-id]");
      if (!owner || !host.contains(owner)) return;
      const messageId = owner.dataset.messageId;
      const text = messageId ? messageText(messageId)?.trim() ?? "" : "";
      if (!messageId || !text) return;
      event.preventDefault();
      setCopied(false);
      openMenu({
        text: text.slice(0, MAX_QUOTED_CONTEXT_CHARS),
        messageId,
        left: event.clientX,
        top: event.clientY + 8,
        above: event.clientY + 60 > window.innerHeight,
        source: "message",
      });
    };

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Element && target.closest("[data-message-action-menu]")) return;
      close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };

    document.addEventListener("selectionchange", updateFromSelection);
    document.addEventListener("contextmenu", onContextMenu);
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("scroll", close, true);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", close);
    window.visualViewport?.addEventListener("resize", close);
    window.visualViewport?.addEventListener("scroll", close);
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      document.removeEventListener("selectionchange", updateFromSelection);
      document.removeEventListener("contextmenu", onContextMenu);
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("scroll", close, true);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", close);
      window.visualViewport?.removeEventListener("resize", close);
      window.visualViewport?.removeEventListener("scroll", close);
    };
  }, [close, containerRef, messageText, openMenu]);

  useEffect(() => () => {
    if (copiedTimerRef.current !== null) window.clearTimeout(copiedTimerRef.current);
  }, []);

  const copy = () => {
    if (!menu) return;
    void copyTextToClipboard(menu.text).then((ok) => {
      if (!ok) {
        close();
        return;
      }
      setCopied(true);
      if (copiedTimerRef.current !== null) window.clearTimeout(copiedTimerRef.current);
      copiedTimerRef.current = window.setTimeout(() => {
        copiedTimerRef.current = null;
        close();
      }, 900);
    });
  };

  const quote = () => {
    if (!menu) return;
    onQuote({ messageId: menu.messageId, text: menu.text });
    window.getSelection()?.removeAllRanges();
    close();
  };

  if (!menu || typeof document === "undefined") return null;

  const copyLabel = copied ? t("message.copiedReply") : t("message.copyReply");
  const actionClassName = cn(
    "inline-flex h-8 items-center gap-1.5 rounded-full px-2.5 text-[12px] font-medium",
    "text-muted-foreground transition-colors hover:bg-accent hover:text-foreground",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
  );

  return createPortal(
    <div
      ref={anchorRef}
      data-message-action-anchor
      className="fixed z-[80]"
      style={{
        left: menu.left,
        top: menu.top,
        transform: menu.above ? "translate(-50%, -100%)" : "translateX(-50%)",
      }}
    >
      <div
        ref={menuRef}
        role="menu"
        data-message-action-menu
        data-state={open ? "open" : "closed"}
        aria-label={t("message.actions")}
        className={cn(
          floatingSurfaceElevationClassName,
          "flex items-center gap-0.5 rounded-full p-0.5",
          "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-90 data-[state=open]:duration-150 data-[state=open]:ease-out",
          "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-90 data-[state=closed]:duration-100 data-[state=closed]:ease-in",
          // Without a fill mode the bar snaps back to full opacity once the exit
          // animation ends but before it unmounts, which reads as a flicker.
          "data-[state=closed]:fill-mode-forwards",
          "motion-reduce:data-[state=open]:animate-none motion-reduce:data-[state=closed]:animate-none",
        )}
        onPointerDown={(event) => event.preventDefault()}
      >
        <button
          ref={firstActionRef}
          type="button"
          role="menuitem"
          data-message-action-copy
          onClick={copy}
          className={actionClassName}
        >
          {copied
            ? <Check className="h-3.5 w-3.5" strokeWidth={2} aria-hidden />
            : <Copy className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />}
          <span>{copyLabel}</span>
        </button>
        <span aria-hidden className="h-4 w-px bg-border/70" />
        <button
          type="button"
          role="menuitem"
          data-message-action-quote
          onClick={quote}
          className={actionClassName}
        >
          <Quote className="h-3.5 w-3.5" strokeWidth={1.75} aria-hidden />
          <span>{t("message.quoteMessage")}</span>
        </button>
      </div>
    </div>,
    document.body,
  );
}
