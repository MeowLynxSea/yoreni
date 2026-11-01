import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";
import { ClipboardPaste, Expand, Hand, Loader2, Minimize2, Monitor, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { createDesktopSession, fetchSandboxStatus, setDesktopControl, type SandboxControlSnapshot, type SandboxStatusPayload } from "@/lib/api";
import { deriveWsUrl } from "@/lib/bootstrap";
import { useClient } from "@/providers/ClientProvider";
import { cn } from "@/lib/utils";

type RfbInstance = {
  viewOnly: boolean;
  scaleViewport: boolean;
  resizeSession: boolean;
  clipViewport: boolean;
  focusOnClick: boolean;
  background: string;
  disconnect(): void;
  clipboardPasteFrom(text: string): void;
  addEventListener(type: string, listener: (event: Event) => void): void;
  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void;
};

type Props = {
  open: boolean;
  onClose: () => void;
  /** Header button the small panel grows out of and collapses back into. */
  anchorRef?: RefObject<HTMLButtonElement | null>;
};

/** The header button closes the panel through the same path as Escape. */
export type ComputerPanelHandle = { close: () => void };

const STATUS_POLL_MS = 2_500;

function desktopWsUrl(ticket: string): string {
  const base = deriveWsUrl("/api/sandbox/desktop/ws", null).split("?")[0];
  return `${base}?ticket=${encodeURIComponent(ticket)}`;
}

/** Grow from, or collapse back into, the header button. */
function animateAnchor(
  element: HTMLElement | null,
  anchor: HTMLElement | null,
  direction: "in" | "out",
): Animation | null {
  if (!element || !anchor || element.animate === undefined) return null;
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return null;
  const rect = element.getBoundingClientRect();
  const target = anchor.getBoundingClientRect();
  if (!rect.width || !target.width) return null;
  const scale = Math.max(0.08, target.width / rect.width);
  const dx = target.left + target.width / 2 - (rect.left + rect.width / 2);
  const dy = target.top + target.height / 2 - (rect.top + rect.height / 2);
  const collapsed = {
    transform: `translate(${dx}px, ${dy}px) scale(${scale})`,
    opacity: 0,
  };
  const open = { transform: "none", opacity: 1 };
  return element.animate(
    direction === "in" ? [collapsed, open] : [open, collapsed],
    {
      duration: 240,
      easing: "cubic-bezier(0.22, 1, 0.36, 1)",
      fill: direction === "out" ? "forwards" : "none",
    },
  );
}

export const ComputerPanel = forwardRef<ComputerPanelHandle, Props>(function ComputerPanel(
  { open, onClose, anchorRef },
  ref,
) {
  const { t } = useTranslation();
  const { client, getToken } = useClient();
  const token = getToken();
  const screenRef = useRef<HTMLDivElement>(null);
  const shellRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<RfbInstance | null>(null);
  const controllerRef = useRef<string | null>(null);
  const generationRef = useRef(0);
  const [view, setView] = useState<"peek" | "full">("peek");
  const [status, setStatus] = useState<SandboxStatusPayload | null>(null);
  const [control, setControl] = useState<SandboxControlSnapshot | null>(null);
  const [phase, setPhase] = useState<"idle" | "connecting" | "connected" | "error">("idle");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [fit, setFit] = useState<{ width: number; height: number } | null>(null);
  const [frame, setFrame] = useState<{ width: number; height: number } | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const morphFrom = useRef<DOMRect | null>(null);
  const grewFromAnchor = useRef(false);
  const [arrowX, setArrowX] = useState<number | null>(null);

  // Grow and shrink the desktop from its small preview rect instead of sliding
  // a differently shaped panel in from the edge.
  // noVNC scales its canvas from the container it was mounted in. Resizing the
  // container is not enough when the switch happens inside one frame, so ask it
  // to recompute after every layout change.
  const reflowScreen = () => {
    const rfb = rfbRef.current;
    if (!rfb) return;
    rfb.scaleViewport = true;
  };

  useLayoutEffect(() => {
    const from = morphFrom.current;
    morphFrom.current = null;
    const element = screenRef.current;
    if (!from || !element || element.animate === undefined) return;
    const to = element.getBoundingClientRect();
    if (!to.width || !to.height) return;
    window.requestAnimationFrame(reflowScreen);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const animation = element.animate(
      [
        {
          transformOrigin: "top left",
          transform: `translate(${from.left - to.left}px, ${from.top - to.top}px) `
            + `scale(${from.width / to.width}, ${from.height / to.height})`,
        },
        { transformOrigin: "top left", transform: "none" },
      ],
      { duration: 320, easing: "cubic-bezier(0.22, 1, 0.36, 1)" },
    );
    void animation.finished
      .then(() => { window.requestAnimationFrame(reflowScreen); })
      .catch(() => undefined);
  }, [view]);

  useEffect(() => {
    if (phase !== "connected") return;
    const frame = window.requestAnimationFrame(reflowScreen);
    return () => window.cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase]);

  useLayoutEffect(() => {
    if (!open || view !== "peek") {
      if (!open) grewFromAnchor.current = false;
      return;
    }
    if (grewFromAnchor.current) return;
    grewFromAnchor.current = true;
    animateAnchor(shellRef.current, anchorRef?.current ?? null, "in");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, view]);

  // The bubble points back at the header button that opened it, so its tip has
  // to follow the button when the window or the header layout changes.
  useLayoutEffect(() => {
    if (!open || view !== "peek") return;
    const measure = () => {
      const shell = shellRef.current;
      const anchor = anchorRef?.current;
      if (!shell || !anchor) return;
      const shellRect = shell.getBoundingClientRect();
      const anchorRect = anchor.getBoundingClientRect();
      if (!shellRect.width || !anchorRect.width) return;
      const center = anchorRect.left + anchorRect.width / 2 - shellRect.left;
      setArrowX(Math.min(Math.max(center, 18), shellRect.width - 18));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, view]);

  const ratio = frame
    ? frame.width / frame.height
    : status && status.desktop.height > 0
      ? status.desktop.width / status.desktop.height
      : 16 / 10;

  useLayoutEffect(() => {
    if (view !== "full") {
      setFit(null);
      return;
    }
    const stage = stageRef.current;
    if (!stage) return;
    const measure = () => {
      const { width, height } = stage.getBoundingClientRect();
      if (!width || !height) return;
      // The largest framebuffer-shaped box the padded stage can hold: matching
      // the VM ratio is what keeps letterboxing out of the view.
      setFit(width / height > ratio
        ? { width: height * ratio, height }
        : { width, height: width / ratio });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(stage);
    return () => observer.disconnect();
  }, [view, ratio]);

  useEffect(() => {
    if (!fit) return;
    const frame = window.requestAnimationFrame(reflowScreen);
    return () => window.cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fit]);

  const switchView = (next: "peek" | "full") => {
    morphFrom.current = screenRef.current?.getBoundingClientRect() ?? null;
    setView(next);
  };

  const disconnect = () => {
    generationRef.current += 1;
    const rfb = rfbRef.current;
    rfbRef.current = null;
    if (rfb) {
      try { rfb.disconnect(); } catch { /* already closed */ }
    }
    setPhase("idle");
  };

  const connect = async () => {
    if (!token || !screenRef.current) return;
    disconnect();
    const generation = generationRef.current;
    setPhase("connecting");
    setError(null);
    controllerRef.current = null;
    try {
      const session = await createDesktopSession(token);
      // A closed panel, a second connect, or a remount must not attach a
      // viewer nobody owns: the relay keeps the controller until it closes.
      if (generation !== generationRef.current || !screenRef.current) return;
      controllerRef.current = session.controller_id;
      setControl(session.control);
      const { default: RFB } = await import("@novnc/novnc");
      if (generation !== generationRef.current || !screenRef.current) return;
      const rfb = new RFB(screenRef.current, desktopWsUrl(session.ticket), {
        shared: true,
      }) as unknown as RfbInstance;
      rfb.background = "#0b0d10";
      rfb.scaleViewport = true;
      rfb.resizeSession = false;
      rfb.clipViewport = false;
      rfb.focusOnClick = true;
      rfb.viewOnly = session.control.owner !== "user";
      rfb.addEventListener("connect", () => {
        setPhase("connected");
        window.requestAnimationFrame(() => {
          // The canvas carries the remote framebuffer size; the image axis is
          // authoritative because a VM may ignore the configured resolution.
          const canvas = screenRef.current?.querySelector("canvas");
          if (canvas?.width) setFrame({ width: canvas.width, height: canvas.height });
        });
      });
      rfb.addEventListener("disconnect", () => {
        if (rfbRef.current === rfb) {
          setPhase("idle");
          setError(t("computer.disconnected"));
        }
      });
      rfb.addEventListener("securityfailure", () => {
        setPhase("error");
        setError(t("computer.authFailed"));
      });
      rfbRef.current = rfb;
    } catch (reason) {
      setPhase("error");
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const refreshStatus = async () => {
    if (!token) return;
    try {
      const next = await fetchSandboxStatus(token);
      setStatus(next);
      setControl(next.control);
      if (rfbRef.current) {
        rfbRef.current.viewOnly = next.control.owner !== "user";
      }
    } catch {
      // keep the last known state; the viewer shows its own connection error
    }
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || document.fullscreenElement) return;
      void (view === "full" ? releaseControl() : close());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, view, control]);

  useEffect(() => {
    if (!open) {
      disconnect();
      setView("peek");
      return;
    }
    void refreshStatus();
    void connect();
    const timer = window.setInterval(() => { void refreshStatus(); }, STATUS_POLL_MS);
    return () => {
      window.clearInterval(timer);
      disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const leaveFullscreen = async () => {
    if (!document.fullscreenElement) return;
    try { await document.exitFullscreen(); } catch { /* already left */ }
  };

  const releaseControl = async () => {
    // The browser's own fullscreen outlives our layout, so drop it first.
    await leaveFullscreen();
    const controller = controllerRef.current;
    if (controller && token && control?.owner === "user") {
      try {
        const next = await setDesktopControl(client, controller, "release");
        setControl(next);
        if (rfbRef.current) rfbRef.current.viewOnly = true;
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
        return;
      }
    }
    // Collapse back to the preview: the stream stays open, view-only again.
    switchView("peek");
  };

  const close = async () => {
    await leaveFullscreen();
    const controller = controllerRef.current;
    if (controller && control?.owner === "user" && token) {
      try { await setDesktopControl(client, controller, "release"); } catch { /* best effort */ }
    }
    const collapse = animateAnchor(shellRef.current, anchorRef?.current ?? null, "out");
    if (collapse !== null) {
      try { await collapse.finished; } catch { /* interrupted */ }
    }
    disconnect();
    setView("peek");
    grewFromAnchor.current = false;
    onClose();
  };

  useImperativeHandle(ref, () => ({ close: () => { void close(); } }));

  const takeControl = async () => {
    const controller = controllerRef.current;
    if (!controller || !token || busy) return;
    setBusy(true);
    try {
      const next = await setDesktopControl(client, controller, "take");
      setControl(next);
      if (rfbRef.current) rfbRef.current.viewOnly = next.owner !== "user";
      // Expand only after the server hands over input authority, so the large
      // view never leaves the user typing into a desktop the agent owns.
      switchView("full");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const toggleFullscreen = async () => {
    if (!shellRef.current) return;
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await shellRef.current.requestFullscreen();
    } catch {
      // Fullscreen can be denied by the browser; the fitted view still works.
    }
  };

  const pasteClipboard = async () => {
    try {
      const text = await navigator.clipboard.readText();
      rfbRef.current?.clipboardPasteFrom(text);
    } catch {
      setError(t("computer.clipboardDenied"));
    }
  };

  if (!open) return null;

  const userControls = control?.owner === "user";
  const desktopReady = Boolean(status?.sandbox.health.desktop_ready);
  const running = status?.sandbox.state === "running";
  const live = running && desktopReady;
  const full = view === "full";
  const stateLabel = !status
    ? t("computer.loading")
    : !live
      ? t("computer.offline")
      : userControls
        ? t("computer.youControl")
        : t("computer.agentControls");
  const phaseMessage = phase === "connecting"
    ? t("computer.connecting")
    : phase === "error"
      ? error ?? t("computer.offline")
      : live
        ? t("computer.disconnected")
        : t("computer.offline");

  const statusDot = (
    <span
      className={cn(
        "h-1.5 w-1.5 shrink-0 rounded-full transition-colors duration-300 motion-reduce:transition-none",
        live ? "bg-emerald-500" : "bg-foreground/25",
        phase === "connecting" ? "animate-pulse" : "",
      )}
      aria-hidden
    />
  );

  return (
    <div
      role="dialog"
      aria-modal={full}
      aria-label={t("computer.title")}
      className={cn("fixed z-50", full ? "inset-0" : "right-3 top-14")}
    >
      <div
        aria-hidden
        className={cn(
          "absolute inset-0 transition-[background-color,backdrop-filter] duration-300 motion-reduce:transition-none",
          full
            ? "bg-[#0b0d10]/45 backdrop-blur-2xl"
            : "pointer-events-none bg-transparent backdrop-blur-none",
        )}
      />

      {!full && arrowX !== null ? (
        <span
          aria-hidden
          style={{ left: arrowX - 7 }}
          className="pointer-events-none absolute -top-[4px] h-3.5 w-3.5 rotate-45 rounded-[3px] border-l border-t border-[color:var(--app-glass-border)] bg-[var(--app-glass)] backdrop-blur-xl animate-in fade-in-0 duration-200 motion-reduce:animate-none"
        />
      ) : null}

      <div
        ref={shellRef}
        className={cn(
          "relative flex flex-col",
          full
            ? "h-full w-full p-4 md:p-10"
            : "w-[min(22rem,calc(100vw-1.5rem))] overflow-hidden rounded-[1.15rem] app-surface-glass shadow-2xl",
        )}
      >
        {full ? (
          <header className="absolute right-6 top-6 z-10 flex items-center gap-0.5 rounded-full app-surface-glass px-1 py-1 animate-in fade-in-0 duration-300 motion-reduce:animate-none">
            <Button type="button" variant="icon" size="icon" className="h-8 w-8"
              aria-label={t("computer.paste")} title={t("computer.paste")}
              onClick={() => { void pasteClipboard(); }}>
              <ClipboardPaste className="h-4 w-4" />
            </Button>
            <Button type="button" variant="icon" size="icon" className="h-8 w-8"
              aria-label={t("computer.fullscreen")} title={t("computer.fullscreen")}
              onClick={() => { void toggleFullscreen(); }}>
              <Expand className="h-4 w-4" />
            </Button>
            <Button type="button" variant="icon" size="icon" className="h-8 w-8"
              aria-label={t("computer.release")} title={t("computer.release")}
              onClick={() => { void releaseControl(); }}>
              <Minimize2 className="h-4 w-4" />
            </Button>
          </header>
        ) : (
          <header className="flex items-center gap-2 rounded-t-[1.15rem] px-3 pb-2 pt-2.5">
            {statusDot}
            <p className="shrink-0 text-ui font-medium tracking-tight text-foreground">
              {t("computer.title")}
            </p>
            <p
              key={stateLabel}
              className="truncate text-caption text-muted-foreground/85 animate-in fade-in-0 duration-200 motion-reduce:animate-none"
            >
              {stateLabel}
            </p>
            <div className="ml-auto flex items-center gap-0.5">
              <Button type="button" variant="icon" size="icon" className="h-7 w-7"
                aria-label={t("computer.take")} title={t("computer.take")}
                disabled={busy || !live}
                onClick={() => { void takeControl(); }}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Hand className="h-4 w-4" />}
              </Button>
              <Button type="button" variant="icon" size="icon" className="h-7 w-7"
                aria-label={t("computer.reconnect")} title={t("computer.reconnect")}
                onClick={() => { void connect(); }}>
                <RotateCw className="h-4 w-4" />
              </Button>
            </div>
          </header>
        )}

        <div
          ref={stageRef}
          className={cn(full ? "relative flex min-h-0 flex-1 items-center justify-center" : "relative")}
        >
        <div
          ref={screenRef}
          style={{ aspectRatio: ratio, ...(full && fit ? { width: fit.width, height: fit.height } : null) }}
          className={cn(
            "relative overflow-hidden bg-[#0b0d10] ring-1 ring-black/25 dark:ring-white/10",
            full ? "max-h-full max-w-full rounded-[1.25rem]" : "w-full",
          )}
        >
          {phase !== "connected" ? (
            <div
              key={`${phase}-${live}`}
              className="absolute inset-0 grid place-items-center px-6 text-center animate-in fade-in-0 duration-300 motion-reduce:animate-none"
            >
              <div className="flex flex-col items-center gap-2.5">
                {phase === "connecting" ? (
                  <Loader2 className="h-4 w-4 animate-spin text-white/45" />
                ) : (
                  <Monitor className="h-5 w-5 text-white/30" strokeWidth={1.5} />
                )}
                <p className="max-w-[18rem] text-caption leading-relaxed text-white/55">
                  {phaseMessage}
                </p>
                <button
                  type="button"
                  onClick={() => { void connect(); }}
                  className="rounded-full px-3 py-1 text-ui-label text-white/75 ring-1 ring-white/15 transition-[background-color,color] duration-150 ease-out motion-reduce:transition-none hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
                >
                  {t("computer.reconnect")}
                </button>
              </div>
            </div>
          ) : null}
        </div>
        </div>

        {error ? (
          <p
            key={error}
            className={cn(
              "truncate text-caption text-destructive/90 animate-in fade-in-0 duration-200 motion-reduce:animate-none",
              full ? "mx-auto mt-2 w-full max-w-xl text-center" : "px-3 pb-2",
            )}
          >
            {error}
          </p>
        ) : null}
      </div>
    </div>
  );
});
