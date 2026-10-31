import { cn } from "@/lib/utils";
import { Disclosure } from "@/components/ui/disclosure";
import { ChevronDown } from "lucide-react";
import { Fragment, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ArrowDown, ArrowLeft, ArrowRight, Eye, EyeOff, Fingerprint, Loader2 } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";
import { HashRouter, Navigate, Route, Routes, useLocation, useMatch, useNavigate, useParams } from "react-router-dom";
import { AmbientBackground } from "@/components/AmbientBackground";
import { AgentRunIndicator } from "@/components/AgentRunIndicator";
import { AppHeader } from "@/components/AppHeader";
import { ChatSearch, type ChatSearchProps } from "@/components/ChatSearch";
import { HomePanel } from "@/components/home/HomePanel";
import { ProjectsPage } from "@/components/projects/ProjectsPage";
import { StartupShell } from "@/components/StartupShell";
import { AgentRecovery } from "@/components/AgentRecovery";
import { MessageBubble } from "@/components/MessageBubble";
import { SettingsIndex } from "@/components/settings/SettingsIndex";
import { isSettingsSectionKey } from "@/components/settings/contracts";
import { MessageActionLayer, type MessageQuoteSelection } from "@/components/thread/MessageActionLayer";
import { ThreadComposer } from "@/components/thread/ThreadComposer";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAgentRun } from "@/hooks/useAgentRun";
import { useProject } from "@/hooks/useProject";
import { ThemeControlProvider, useTheme, useThemeControl } from "@/hooks/useTheme";
import { useAppViewport } from "@/hooks/useAppViewport";
import { useReadingTypography } from "@/hooks/useReadingTypography";
import { BootstrapAuthRequiredError, clearSavedSecret, consumeUrlBootstrapSecret, deriveWsUrl, fetchBootstrap, loadSavedSecret, saveSecret } from "@/lib/bootstrap";
import { OnboardingFlow } from "@/components/onboarding/OnboardingFlow";
import type { SendAttachment } from "@/lib/agent-input";
import { authenticateWithPasskey, fetchAuthStatus, fetchChatSearch, fetchChatSearchSemantic, fetchOnboarding, requestPasskeyAssertion } from "@/lib/api";
import { assertPasskey, passkeysNeedSecureContext, passkeysSupported } from "@/lib/webauthn";
import {
  parseChatSearchResult,
  parseChatSemanticResult,
  type ChatSearchHit,
} from "@/lib/chat-search";
import { formatMessageGroupTime } from "@/lib/format-time";
import { readHomeView, readLastLocation, restoreLocation, writeHomeView, writeLastLocation, writeLastProjectsPath, type HomeView } from "@/lib/local-ui-state";
import { messageTimeGroups } from "@/lib/message-groups";
import { messageCopyText } from "@/lib/message-text";
import { createRuntimeHost } from "@/lib/runtime";
import { formatQuotedUserMessage } from "@/lib/user-message-quote";
import { YoreniClient } from "@/lib/yoreni-client";
import type { AuthStatus, BootstrapResponse, OnboardingSnapshot, UIMessage } from "@/lib/types";
import { ClientProvider, useClient } from "@/providers/ClientProvider";

const SettingsView = lazy(() => import("@/components/settings/SettingsView").then((module) => ({ default: module.SettingsView })));
type Ready = { client: YoreniClient; boot: BootstrapResponse };
const GREETINGS = ["workOn", "start", "build", "tackle"] as const;

function AuthScreen({ failed, onSecret }: { failed: boolean; onSecret: (secret: string) => void }) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState("");
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [passkeyBusy, setPasskeyBusy] = useState(false);
  const [validationError, setValidationError] = useState<"required" | "invalid" | null>(
    failed ? "invalid" : null,
  );
  const passkeyRegistered = Boolean(authStatus && authStatus.passkey_count > 0);
  const errorMessage = validationError
    && (validationError !== "invalid" || !passkeyRegistered)
    ? t(`app.auth.${validationError}`)
    : null;

  useEffect(() => {
    if (!passkeysSupported()) return;
    let live = true;
    fetchAuthStatus().then(
      (status) => { if (live) setAuthStatus(status); },
      () => { /* older gateways simply keep the password form */ },
    );
    return () => { live = false; };
  }, []);

  const passkeyAvailable = passkeyRegistered && passkeysSupported();
  const passkeyHint = Boolean(authStatus) && !passkeyRegistered && passkeysSupported();
  const needsSecureContext = passkeysNeedSecureContext();

  useEffect(() => {
    if (!validationError) return;
    const timeout = window.setTimeout(() => setValidationError(null), 3_000);
    return () => window.clearTimeout(timeout);
  }, [validationError]);

  useEffect(() => {
    if (failed) setSubmitting(false);
  }, [failed]);

  const handlePasskey = async () => {
    if (passkeyBusy) return;
    setPasskeyBusy(true);
    setValidationError(null);
    try {
      const options = await requestPasskeyAssertion();
      const assertion = await assertPasskey(options);
      if (!assertion) return;
      const result = await authenticateWithPasskey(assertion);
      setSubmitting(true);
      onSecret(result.ticket);
    } catch {
      setValidationError("invalid");
    } finally {
      setPasskeyBusy(false);
    }
  };

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const secret = value.trim();
    if (!secret) {
      setValue("");
      setValidationError("required");
      inputRef.current?.focus();
      return;
    }
    setSubmitting(true);
    onSecret(secret);
  };

  return (
    <main className="relative flex h-full w-full items-center justify-center overflow-y-auto p-4 sm:p-6">
      <AmbientBackground />
      <section className="message-enter app-surface-glass relative z-10 grid w-full max-w-[62rem] overflow-hidden rounded-[2rem] md:grid-cols-[1.05fr_0.95fr]">
        <div className="relative hidden min-h-[36rem] overflow-hidden md:flex md:flex-col md:justify-between">
          <div className="absolute inset-0 bg-[radial-gradient(80%_70%_at_20%_0%,rgba(255,255,255,0.72),transparent_60%),linear-gradient(155deg,rgba(91,164,173,0.16),rgba(191,90,66,0.13))]" />
          <div className="relative flex flex-1 items-center justify-center p-10">
            <img
              src="/brand/yoreni_mark.svg"
              alt=""
              draggable={false}
              className="h-56 w-56 select-none rounded-[2rem] shadow-[0_30px_80px_-30px_rgba(21,34,50,0.4)]"
            />
          </div>
          <div className="relative px-10 pb-10">
            <p className="display-serif text-[2.75rem] leading-none text-foreground/90">Yoreni</p>
          </div>
        </div>

        <div className="flex flex-col justify-center px-6 py-10 sm:px-10 lg:px-14">
          <div className="mx-auto w-full max-w-[19rem]">
            <div className="mb-8 flex items-center gap-3 md:hidden">
              <img src="/brand/yoreni_mark.svg" alt="" draggable={false} className="h-11 w-11 rounded-xl" />
              <span className="display-serif text-[1.9rem] leading-none">Yoreni</span>
            </div>
            <h1 id="webui-auth-title" className="display-serif text-[2rem] leading-[1.1] text-foreground sm:text-[2.25rem]">
              {t("app.auth.title")}
            </h1>
            {passkeyAvailable ? (
              <div className="mt-8 space-y-4">
                <Button
                  type="button"
                  onClick={() => { void handlePasskey(); }}
                  disabled={passkeyBusy}
                  className="h-11 w-full rounded-full"
                >
                  {passkeyBusy ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
                  ) : (
                    <Fingerprint className="mr-2 h-4 w-4" strokeWidth={1.75} aria-hidden />
                  )}
                  {t("app.auth.passkey.action")}
                </Button>
                <p className="text-center text-[12px] text-muted-foreground/80">
                  {t("app.auth.passkey.orPassword")}
                </p>
              </div>
            ) : null}
            <form onSubmit={handleSubmit} className={passkeyAvailable ? "mt-4" : "mt-8"}>
              <div className="relative">
                <Input
                  ref={inputRef}
                  id="webui-access-password"
                  name="webui-access-password"
                  aria-label={t("app.auth.label")}
                  type={passwordVisible ? "text" : "password"}
                  autoComplete="current-password"
                  value={value}
                  onChange={(event) => {
                    setValue(event.target.value);
                    setValidationError(null);
                  }}
                  disabled={submitting}
                  aria-invalid={validationError ? true : undefined}
                  aria-describedby={validationError ? "webui-auth-error" : undefined}
                  placeholder={errorMessage ?? t("app.auth.label")}
                  className={cn(
                    "h-12 rounded-full border-foreground/10 bg-background/55 pl-5 pr-24 text-[15px] backdrop-blur-md",
                    validationError && "placeholder:text-red-600 dark:placeholder:text-red-400",
                  )}
                  autoFocus
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  disabled={submitting}
                  aria-label={t(passwordVisible ? "app.auth.hidePassword" : "app.auth.showPassword")}
                  aria-controls="webui-access-password"
                  onClick={() => setPasswordVisible((visible) => !visible)}
                  className="absolute right-12 top-1/2 h-9 w-9 -translate-y-1/2 rounded-full text-muted-foreground hover:text-foreground"
                >
                  {passwordVisible ? (
                    <EyeOff className="h-4 w-4" strokeWidth={1.75} aria-hidden />
                  ) : (
                    <Eye className="h-4 w-4" strokeWidth={1.75} aria-hidden />
                  )}
                </Button>
                <Button
                  type="submit"
                  size="icon"
                  disabled={submitting}
                  aria-label={t("app.auth.submit")}
                  title={t("app.auth.submit")}
                  className="absolute right-1.5 top-1/2 h-9 w-9 -translate-y-1/2 rounded-full"
                >
                  <ArrowRight className="h-4 w-4" strokeWidth={1.75} aria-hidden />
                </Button>
              </div>
              {errorMessage ? (
                <p id="webui-auth-error" role="alert" className="sr-only">
                  {errorMessage}
                </p>
              ) : null}
            </form>
            {needsSecureContext || passkeyHint ? (
              <p className="mt-4 text-center text-[11.5px] leading-5 text-muted-foreground/80">
                {t(
                  needsSecureContext
                    ? "app.auth.passkey.secureContext"
                    : "app.auth.passkey.hint",
                )}
              </p>
            ) : null}
            <Disclosure
              className="mt-5"
              summaryClassName="flex min-h-11 items-center justify-center gap-1.5 rounded-full text-[13px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
              contentClassName="rounded-[1.25rem] border border-white/50 bg-white/45 p-4 text-[13px] leading-6 text-muted-foreground backdrop-blur-md dark:border-white/10 dark:bg-white/[0.04]"
              summary={(
                <>
                  <span>{t("app.auth.helpTitle")}</span>
                  <ChevronDown className="h-3.5 w-3.5 shrink-0 transition-transform group-data-[state=open]/disclosure:rotate-180 motion-reduce:transition-none" strokeWidth={1.5} aria-hidden />
                </>
              )}
            >
              <p>{t("app.auth.helpConfig")}</p>
              <code className="mt-2 block rounded-xl bg-background/70 px-3 py-2 font-mono text-xs text-foreground [overflow-wrap:anywhere]">
                ~/.yoreni/config.json
              </code>
              <p className="mt-4">{t("app.auth.helpSecret")}</p>
              <code className="mt-2 block rounded-xl bg-background/70 px-3 py-2 font-mono text-xs text-foreground [overflow-wrap:anywhere]">
                channels.websocket.tokenIssueSecret
              </code>
              <p className="mt-3">
                <Trans
                  i18nKey="app.auth.helpFallback"
                  components={{ code: <code className="font-mono text-xs text-foreground [overflow-wrap:anywhere]" /> }}
                />
              </p>
            </Disclosure>
          </div>
        </div>
      </section>
    </main>
  );
}

function BootError({ error, onRetry }: { error: string; onRetry: () => void }) {
  const { t } = useTranslation();
  return (
    <main className="relative flex h-full w-full items-center justify-center p-6">
      <AmbientBackground />
      <section className="message-enter app-surface-glass relative z-10 w-full max-w-md rounded-[2rem] p-8 text-center">
        <h1 className="display-serif text-[1.9rem] leading-tight text-foreground">{t("app.error.title")}</h1>
        <p className="mt-3 break-words text-[13px] leading-6 text-muted-foreground">{error}</p>
        <Button onClick={onRetry} className="mt-6">
          {t("common.retry")}
        </Button>
      </section>
    </main>
  );
}

/**
 * The URL is the only navigation state. Every route change is remembered, and a
 * bare start reopens the last page; a deep link always wins over the memory.
 */
function NavigationPersistence() {
  const location = useLocation();
  const navigate = useNavigate();
  const restored = useRef(false);
  useEffect(() => {
    const current = `${location.pathname}${location.search}`;
    // Where the reader stood inside Projects, so the entry can reopen it.
    // Leaving for another part of the app keeps that place on record.
    if (location.pathname.startsWith("/projects")) writeLastProjectsPath(location.pathname);
    if (!restored.current) {
      restored.current = true;
      const restoredPath = restoreLocation(current, readLastLocation());
      if (restoredPath) {
        navigate(restoredPath, { replace: true });
        return;
      }
    }
    writeLastLocation(current);
  }, [location.pathname, location.search, navigate]);
  return null;
}

export default function App() {
  useAppViewport();
  useReadingTypography();
  const [ready, setReady] = useState<Ready | null>(null);
  const [auth, setAuth] = useState(false);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [onboarding, setOnboarding] = useState<OnboardingSnapshot | null>(null);
  const [onboardingError, setOnboardingError] = useState<string | null>(null);
  const [onboardingAttempt, setOnboardingAttempt] = useState(0);
  const secret = useRef("");
  const clientRef = useRef<YoreniClient | null>(null);
  const generation = useRef(0);
  const refresh = useCallback(async () => {
    const boot = await fetchBootstrap("", secret.current);
    const contract = boot.terminal?.webui;
    if (!contract || contract.min_protocol > 2 || contract.max_protocol < 2 || !contract.capabilities?.includes("webui.core.v2")) throw new Error("WebUI protocol 2 is required");
    return boot;
  }, []);
  const applyAuthSecret = useCallback((next: string) => {
    secret.current = next;
    saveSecret(next);
  }, []);
  const start = useCallback(async (value: string) => {
    const attempt = ++generation.current;
    clientRef.current?.close(); clientRef.current = null;
    secret.current = value; setAuth(false); setError(null); setReady(null); setOnboarding(null);
    try {
      const boot = await refresh();
      if (attempt !== generation.current) return;
      if (value) saveSecret(value);
      const host = createRuntimeHost(boot.runtime_surface ?? "browser", boot.runtime_capabilities);
      const client = new YoreniClient({
        url: deriveWsUrl(boot.ws_path, boot.token, boot.ws_url), socketFactory: host.socketFactory,
        maxFrameBytes: boot.limits?.transport.max_frame_bytes,
        onReauth: async () => {
          try {
            const next = await refresh();
            if (attempt !== generation.current) return null;
            setReady({ client, boot: next });
            return deriveWsUrl(next.ws_path, next.token, next.ws_url);
          } catch (reason) {
            if (reason instanceof BootstrapAuthRequiredError) { client.close(); setReady(null); setAuth(true); }
            return null;
          }
        },
      });
      clientRef.current = client; setReady({ client, boot }); client.connect();
    } catch (reason) {
      if (attempt !== generation.current) return;
      if (reason instanceof BootstrapAuthRequiredError) { setAuth(true); setFailed(Boolean(value)); }
      else setError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [refresh]);
  useEffect(() => { void start(consumeUrlBootstrapSecret() || loadSavedSecret()); return () => { ++generation.current; clientRef.current?.close(); }; }, [start]);
  useEffect(() => {
    if (!ready?.boot.expires_in) return;
    const timer = setTimeout(() => { void refresh().then((boot) => {
      if (clientRef.current !== ready.client) return;
      ready.client.updateUrl(deriveWsUrl(boot.ws_path, boot.token, boot.ws_url)); setReady({ client: ready.client, boot });
    }).catch(() => {}); }, Math.max(5000, ready.boot.expires_in * 1000 - 30000));
    return () => clearTimeout(timer);
  }, [ready, refresh]);
  useEffect(() => {
    if (!ready) return;
    let live = true;
    setOnboardingError(null);
    fetchOnboarding(ready.boot.api_token ?? "").then(
      (snapshot) => { if (live) setOnboarding(snapshot); },
      (reason: unknown) => {
        if (!live) return;
        setOnboardingError(reason instanceof Error ? reason.message : String(reason));
      },
    );
    return () => { live = false; };
  }, [ready, onboardingAttempt]);
  if (auth) return <AuthScreen failed={failed} onSecret={(value) => { void start(value); }} />;
  if (error) return <BootError error={error} onRetry={() => { void start(secret.current); }} />;
  if (onboardingError) return <BootError error={onboardingError} onRetry={() => setOnboardingAttempt((attempt) => attempt + 1)} />;
  if (!ready) return <StartupShell />;
  if (!onboarding) return <StartupShell />;
  if (onboarding.status !== "completed") {
    return <OnboardingFlow
      client={ready.client}
      apiToken={ready.boot.api_token ?? ""}
      initial={onboarding}
      onTokenSet={(value) => { secret.current = value; saveSecret(value); }}
      onComplete={() => {
        setOnboarding({ ...onboarding, status: "completed" });
        void refresh().then((boot) => {
          if (clientRef.current !== ready.client) return;
          setReady({ client: ready.client, boot });
        }).catch(() => {});
      }}
    />;
  }
  return <ClientProvider client={ready.client} token={ready.boot.api_token ?? ""} modelName={ready.boot.model_name ?? null} ingressLimits={ready.boot.limits ?? null} webuiCapabilities={ready.boot.terminal?.webui?.capabilities ?? []} onAuthSecretChange={applyAuthSecret}>
    <HashRouter>
      <NavigationPersistence />
      <AgentShell onLogout={() => { ++generation.current; ready.client.close(); clearSavedSecret(); window.history.replaceState(null, "", window.location.pathname + window.location.search); setReady(null); setAuth(true); }} />
    </HashRouter>
  </ClientProvider>;
}

function HistorySkeleton() {
  const { t } = useTranslation();
  return (
    <div className="thread-scroll min-h-0 flex-1 overflow-hidden">
      <div role="status" aria-busy="true" aria-label={t("thread.loadingConversation")}
        className="mx-auto flex min-h-full w-full max-w-[44rem] flex-col justify-end gap-6 px-5 pb-10 pt-10 sm:px-6 sm:pb-12">
        <div className="thread-skeleton-bar ml-auto h-9 w-[36%]" />
        <div className="rounded-[1.6rem] border border-white/50 bg-white/35 p-5 backdrop-blur-md dark:border-white/10 dark:bg-white/[0.04]">
          <div className="space-y-3">
            <div className="thread-skeleton-bar h-3.5 w-[68%]" />
            <div className="thread-skeleton-bar h-3.5 w-full" />
            <div className="thread-skeleton-bar h-3.5 w-[52%]" />
          </div>
        </div>
        <div className="thread-skeleton-bar ml-auto h-9 w-[28%]" />
      </div>
    </div>
  );
}

function MessageTimeDivider({ timestamp }: { timestamp: number }) {
  const label = formatMessageGroupTime(timestamp);
  if (!label) return null;
  return (
    <div data-message-time-divider className="flex justify-center">
      <span className="text-[11px] leading-4 tabular-nums text-muted-foreground/70">{label}</span>
    </div>
  );
}

/**
 * A transcript row that keeps the browser's work proportional to what is on
 * screen. A row far outside the viewport is replaced by a spacer holding the
 * height it measured, so the scroll geometry stays exact: unlike an estimated
 * placeholder, loading an older page never resizes the rows the reader is
 * standing on. The spacer keeps the message ID so quotes and search can still
 * land on it. Cards carrying media stay mounted: their height depends on images
 * that finish loading later, and remounting one would reload it.
 */
function ThreadMessageRow({ messageId, pinned = false, children }: {
  messageId: string;
  pinned?: boolean;
  children: ReactNode;
}) {
  const rowRef = useRef<HTMLDivElement>(null);
  const heightRef = useRef(0);
  const [rendered, setRendered] = useState(true);
  useEffect(() => {
    const row = rowRef.current;
    if (!row || pinned || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (!entry) return;
      if (entry.isIntersecting) {
        setRendered(true);
        return;
      }
      const height = entry.boundingClientRect.height;
      // A row the browser has not laid out yet collapses to nothing, so it
      // stays rendered until it reports the height it occupies.
      if (height <= 0) return;
      heightRef.current = height;
      setRendered(false);
    }, { rootMargin: "800px 0px" });
    observer.observe(row);
    return () => observer.disconnect();
  }, [pinned]);
  return (
    <div
      ref={rowRef}
      {...(rendered
        ? {}
        : { "data-message-id": messageId, style: { height: heightRef.current } })}
    >
      {rendered ? children : null}
    </div>
  );
}

/**
 * Rows that draw nothing still take the transcript's row gap, so an in-flight
 * run would turn every activity step into a blank band. This mirrors what
 * `MessageBubble` renders, which returns null for exactly these messages.
 */
function rendersInTranscript(message: UIMessage): boolean {
  if (message.kind === "trace") return false;
  if (message.kind === "compaction" && message.compaction) return true;
  if (message.role !== "assistant") return true;
  return message.content.trim().length > 0
    || message.compactReply !== undefined
    || (message.media?.length ?? 0) > 0;
}

function carriesMedia(message: UIMessage): boolean {
  return Boolean(message.rich || message.media?.length);
}

function ChatView({ greeting, run, model, onOpenSettings, focusMessageId, onFocusHandled, onBackToProjects, projectId }: {
  greeting: string;
  run: ReturnType<typeof useAgentRun>;
  model: string | null;
  onOpenSettings?: () => void;
  focusMessageId?: string | null;
  onFocusHandled?: () => void;
  onBackToProjects?: () => void;
  projectId?: string | null;
}) {
  const { t } = useTranslation();
  const viewport = useRef<HTMLDivElement>(null);
  const thread = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const [atBottom, setAtBottom] = useState(true);
  const highlightTimer = useRef<number | null>(null);
  const [quote, setQuote] = useState<MessageQuoteSelection | null>(null);
  const [quoteTarget, setQuoteTarget] = useState<string | null>(null);
  const running = run.active || run.sending;
  const loadingHistory = !run.historyLoaded && run.messages.length === 0;
  const empty = run.messages.length === 0 && !loadingHistory && !running;
  const groups = useMemo(
    () => messageTimeGroups(run.messages.filter(rendersInTranscript)),
    [run.messages],
  );
  useEffect(() => { if (following.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight; }, [run.messages, running]);
  const scrollToBottom = useCallback(() => {
    const element = viewport.current;
    if (!element) return;
    following.current = true;
    setAtBottom(true);
    element.scrollTo({ top: element.scrollHeight, behavior: "smooth" });
  }, []);
  useEffect(() => () => {
    if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current);
  }, []);
  const messageText = useCallback((messageId: string) => {
    const message = run.messages.find((entry) => entry.id === messageId);
    return message ? messageCopyText(message, t) : null;
  }, [run.messages, t]);
  const jumpToQuote = useCallback((targetId: string | null, text: string) => {
    const root = thread.current;
    if (!root) return;
    const nodes = Array.from(root.querySelectorAll<HTMLElement>("[data-message-id]"));
    const wanted = text.replace(/\s+/g, " ").trim().slice(0, 24);
    const target = (targetId ? nodes.find((node) => node.dataset.messageId === targetId) : undefined)
      ?? (wanted
        ? nodes.find((node) => (node.textContent ?? "").replace(/\s+/g, " ").includes(wanted))
        : undefined);
    if (!target) return;
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    const landed = target.dataset.messageId ?? null;
    setQuoteTarget(landed);
    if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current);
    highlightTimer.current = window.setTimeout(() => setQuoteTarget(null), 1_400);
  }, []);
  const send = async (content: string, attachments?: SendAttachment[]) => {
    const pending = quote;
    const composed = pending ? formatQuotedUserMessage(content, pending.text, pending.messageId) : content;
    await run.send(composed, attachments ?? []);
    if (pending) setQuote(null);
  };
  // A search result lands on its message the same way a quote jump does.
  useEffect(() => {
    if (!focusMessageId) return;
    const root = thread.current;
    if (!root) return;
    const target = Array.from(root.querySelectorAll<HTMLElement>("[data-message-id]"))
      .find((node) => node.dataset.messageId === focusMessageId);
    if (!target) return;
    target.scrollIntoView({ block: "center", behavior: "smooth" });
    setQuoteTarget(focusMessageId);
    if (highlightTimer.current !== null) window.clearTimeout(highlightTimer.current);
    highlightTimer.current = window.setTimeout(() => setQuoteTarget(null), 1_400);
    onFocusHandled?.();
  }, [focusMessageId, groups, onFocusHandled]);
  return (
    <main className="relative flex min-h-0 flex-1 flex-col">
      <AgentRecovery projectId={projectId} />
      <MessageActionLayer
        containerRef={thread}
        messageText={messageText}
        onQuote={setQuote}
      />
      <div className={cn("flex min-h-0 flex-1 flex-col", empty && "justify-center")}>
        {loadingHistory ? (
          <HistorySkeleton />
        ) : empty ? (
          <div className="px-5 pb-8 pt-4 sm:pb-12">
            {onBackToProjects ? (
              <div className="mx-auto mb-4 flex max-w-[38rem] justify-center">
                <button
                  type="button"
                  onClick={onBackToProjects}
                  className="inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
                >
                  <ArrowLeft className="h-3.5 w-3.5" strokeWidth={1.6} aria-hidden />
                  {t("projects.allProjects")}
                </button>
              </div>
            ) : null}
            <h1 className="message-enter display-serif mx-auto max-w-[38rem] text-balance text-center text-[clamp(1.6rem,3.4vw,2.5rem)] leading-[1.15] text-foreground/90">
              {greeting}
            </h1>
          </div>
        ) : (
          <div
            ref={viewport}
            onScroll={() => {
              const element = viewport.current;
              if (!element) return;
              const bottom = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
              following.current = bottom;
              setAtBottom(bottom);
            }}
            className="thread-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain"
          >
            <div ref={thread} data-testid="thread-message-region" className="mx-auto flex min-h-full w-full max-w-[47.5rem] flex-col justify-end gap-6 px-5 pb-10 pt-10 sm:px-6 sm:pb-12 md:px-[3.25rem]">
              {groups.map((group) => (
                <Fragment key={group.key}>
                  <MessageTimeDivider timestamp={group.startedAt} />
                  {group.messages.map((message) => (
                    <ThreadMessageRow
                      key={message.id}
                      messageId={message.id}
                      pinned={carriesMedia(message)}
                    >
                      <MessageBubble
                        message={message}
                        onJumpToQuote={jumpToQuote}
                        quoteTarget={quoteTarget === message.id}
                      />
                    </ThreadMessageRow>
                  ))}
                </Fragment>
              ))}
              {running ? <AgentRunIndicator key="agent-run" activity={run.activity} className="-mt-3" /> : null}
            </div>
          </div>
        )}
        <div className={cn("shrink-0", empty ? "px-5 pb-8 sm:pb-10" : "px-4 pb-4 sm:px-6 sm:pb-6")}>
          <div className={cn("message-enter relative mx-auto w-full [animation-delay:90ms]", empty ? "max-w-[42rem]" : "max-w-[44rem]")}>
            {!empty && !loadingHistory ? (
              <button
                type="button"
                aria-label={t("thread.scrollToBottom")}
                title={t("thread.scrollToBottom")}
                tabIndex={atBottom ? -1 : 0}
                onClick={scrollToBottom}
                className={cn(
                  "absolute bottom-full right-0 z-10 mb-3 grid h-9 w-9 place-items-center rounded-full",
                  "border border-border/60 bg-background/90 text-muted-foreground backdrop-blur",
                  "shadow-[0_6px_20px_-8px_rgba(24,36,58,0.45)] transition-[opacity,color] hover:text-foreground",
                  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  "motion-reduce:transition-none",
                  atBottom && "pointer-events-none opacity-0",
                )}
              >
                <ArrowDown className="h-4 w-4" strokeWidth={1.75} aria-hidden />
              </button>
            ) : null}
            {run.error ? (
              <p role="alert" className="mx-auto mb-2 w-fit rounded-full bg-destructive/10 px-3 py-1.5 text-[12px] text-destructive">
                {run.error}
              </p>
            ) : null}
            <ThreadComposer
              onSend={send}
              onStop={() => { void run.stop(); }}
              isStreaming={run.active}
              variant={empty ? "hero" : "thread"}
              persistDraft
              modelLabel={model}
              onModelBadgeClick={onOpenSettings}
              quotedContext={quote?.text ?? null}
              onClearQuote={() => setQuote(null)}
            />
          </div>
        </div>
      </div>
    </main>
  );
}

function SettingsSectionRoute({
  onLogout,
  onModelNameChange,
  onStartAutomationChat,
  onBackToChat,
}: {
  onLogout: () => void;
  onModelNameChange: (model: string | null) => void;
  onStartAutomationChat: (text: string) => boolean;
  onBackToChat: () => void;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { section } = useParams();
  const { preference, setTheme } = useThemeControl();
  if (!isSettingsSectionKey(section)) return <Navigate to="/settings" replace />;
  return <div className="flex min-h-0 flex-1 flex-col">
    <Suspense fallback={<StartupShell embedded surface="settings" />}>
      <SettingsView
        themePreference={preference}
        showSidebar={false}
        initialSection={section}
        backLabel={t("settings.sidebar.title")}
        onBack={() => navigate("/settings", { viewTransition: true })}
        onSectionChange={(next) => navigate(`/settings/${next}`, { replace: true, viewTransition: true })}
        onChangeTheme={setTheme}
        onBackToChat={onBackToChat}
        onModelNameChange={onModelNameChange}
        onLogout={onLogout}
        onStartAutomationChat={onStartAutomationChat}
      />
    </Suspense>
  </div>;
}

function AgentShell({ onLogout }: { onLogout: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const control = useTheme();
  const { toggle, theme } = control;
  const { client, getToken, modelName } = useClient();
  const [model, setModel] = useState<string | null>(modelName ?? null);
  const [homeView, setHomeView] = useState<HomeView>(readHomeView);
  const [searchOpen, setSearchOpen] = useState(false);
  const [focusMessageId, setFocusMessageId] = useState<string | null>(null);
  const changeHomeView = useCallback((view: HomeView) => {
    setHomeView(view);
    writeHomeView(view);
  }, []);
  const projectMatch = useMatch("/projects/:projectId");
  const projectId = projectMatch?.params.projectId ?? null;
  const project = useProject(projectId);
  const run = useAgentRun(projectId);
  const greeting = useMemo(() => {
    const key = GREETINGS[Math.floor(Math.random() * GREETINGS.length)];
    return t(`thread.empty.greetings.${key}`);
  }, [t]);
  useEffect(() => client.onRuntimeModelUpdate(setModel), [client]);
  // Bootstrap data can arrive after this shell mounts (for example when setup
  // just configured the first model), so follow the new default unless the
  // user already picked something else in this window.
  const previousModelName = useRef(modelName);
  useEffect(() => {
    if (modelName === previousModelName.current) return;
    const previous = previousModelName.current;
    previousModelName.current = modelName;
    setModel((current) => (current === null || current === previous ? modelName : current));
  }, [modelName]);
  const location = useLocation();
  const homeOpen = location.pathname === "/" && homeView === "home";
  // A linked or remembered project can be gone; the transcript request is what
  // reports it, so the list is the only page left to show.
  useEffect(() => {
    if (projectId && run.projectMissing) navigate("/projects", { replace: true });
  }, [navigate, projectId, run.projectMissing]);
  const clearFocusMessage = useCallback(() => setFocusMessageId(null), []);
  const focusSearchResult = useCallback(async (hit: ChatSearchHit) => {
    if (!projectId) {
      changeHomeView("chat");
      if (location.pathname !== "/") navigate("/", { viewTransition: true });
    }
    // Search covers the whole store, so the target may predate this window.
    let present = run.messages.some((message) => message.id === hit.id);
    while (!present) {
      const page = await run.loadOlder().catch(() => null);
      if (!page || page.length === 0) break;
      present = page.some((message) => message.id === hit.id);
    }
    if (present) setFocusMessageId(hit.id);
  }, [changeHomeView, location.pathname, navigate, projectId, run]);
  const requestLocalSearch: ChatSearchProps["requestLocal"] = useCallback(async (query) => {
    return parseChatSearchResult(await fetchChatSearch(getToken(), query, projectId));
  }, [getToken, projectId]);
  const requestSemanticSearch: ChatSearchProps["requestSemantic"] = useCallback(async (query) => {
    return parseChatSemanticResult(await fetchChatSearchSemantic(getToken(), query, projectId));
  }, [getToken, projectId]);
  return <ThemeControlProvider control={control}>
    <AmbientBackground />
    <div className="relative z-10 flex h-full min-h-0 w-full text-foreground">
      <HomePanel open={homeOpen} />
      <div className={cn("relative flex min-h-0 min-w-0 flex-1 flex-col", homeOpen && "hidden md:flex")}>
      <div className="h-16 shrink-0" aria-hidden />
      <div className="app-route-view flex min-h-0 flex-1 flex-col">
      <Routes>
        <Route path="/" element={<ChatView greeting={greeting} run={run} model={model} onOpenSettings={() => navigate("/settings", { viewTransition: true })} focusMessageId={focusMessageId} onFocusHandled={clearFocusMessage} />} />
        <Route path="/projects" element={<ProjectsPage />} />
        <Route path="/projects/:projectId" element={
          <ChatView
            greeting={project?.name ?? ""}
            run={run}
            model={model}
            onOpenSettings={() => navigate("/settings", { viewTransition: true })}
            onBackToProjects={() => navigate("/projects", { viewTransition: true })}
            projectId={projectId}
            focusMessageId={focusMessageId}
            onFocusHandled={clearFocusMessage}
          />
        } />
        <Route path="/settings" element={
          <SettingsIndex
            onOpenSection={(section) => navigate(`/settings/${section}`, { viewTransition: true })}
            onLogout={onLogout}
          />
        } />
        <Route path="/settings/:section" element={
          <SettingsSectionRoute
            onLogout={onLogout}
            onModelNameChange={setModel}
            onStartAutomationChat={(text) => { changeHomeView("chat"); navigate("/", { viewTransition: true }); void run.send(text); return true; }}
            onBackToChat={() => { changeHomeView("chat"); navigate("/", { viewTransition: true }); }}
          />
        } />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      </div>
      </div>
      <AppHeader
        theme={theme}
        onToggleTheme={toggle}
        onOpenSearch={() => setSearchOpen(true)}
        homeView={homeView}
        onHomeViewChange={changeHomeView}
      />
      <ChatSearch
        open={searchOpen}
        onOpenChange={setSearchOpen}
        onSelect={focusSearchResult}
        requestLocal={requestLocalSearch}
        requestSemantic={requestSemanticSearch}
      />
    </div>
  </ThemeControlProvider>;
}
