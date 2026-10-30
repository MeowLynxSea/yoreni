import { cn } from "@/lib/utils";
import { Disclosure } from "@/components/ui/disclosure";
import { ChevronDown } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Eye, EyeOff } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";
import { HashRouter, Navigate, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { AmbientBackground } from "@/components/AmbientBackground";
import { AgentRunIndicator } from "@/components/AgentRunIndicator";
import { AppHeader } from "@/components/AppHeader";
import { HomePanel } from "@/components/home/HomePanel";
import { StartupShell } from "@/components/StartupShell";
import { AgentRecovery } from "@/components/AgentRecovery";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { MessageBubble } from "@/components/MessageBubble";
import { SettingsIndex } from "@/components/settings/SettingsIndex";
import { isSettingsSectionKey } from "@/components/settings/contracts";
import { ThreadComposer } from "@/components/thread/ThreadComposer";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAgentRun } from "@/hooks/useAgentRun";
import { ThemeControlProvider, useTheme, useThemeControl } from "@/hooks/useTheme";
import { useAppViewport } from "@/hooks/useAppViewport";
import { useReadingTypography } from "@/hooks/useReadingTypography";
import { BootstrapAuthRequiredError, clearSavedSecret, consumeUrlBootstrapSecret, deriveWsUrl, fetchBootstrap, loadSavedSecret, saveSecret } from "@/lib/bootstrap";
import { createRuntimeHost } from "@/lib/runtime";
import { YoreniClient } from "@/lib/yoreni-client";
import type { BootstrapResponse } from "@/lib/types";
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
  const [validationError, setValidationError] = useState<"required" | "invalid" | null>(
    failed ? "invalid" : null,
  );
  const errorMessage = validationError ? t(`app.auth.${validationError}`) : null;

  useEffect(() => {
    if (!validationError) return;
    const timeout = window.setTimeout(() => setValidationError(null), 3_000);
    return () => window.clearTimeout(timeout);
  }, [validationError]);

  useEffect(() => {
    if (failed) setSubmitting(false);
  }, [failed]);

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
      <div className="absolute right-4 top-4 z-20">
        <LanguageSwitcher collapsed className="text-muted-foreground" />
      </div>
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
            <p className="display-serif text-[2.75rem] leading-none text-foreground/90">yoreni</p>
          </div>
        </div>

        <div className="flex flex-col justify-center px-6 py-10 sm:px-10 lg:px-14">
          <div className="mx-auto w-full max-w-[19rem]">
            <div className="mb-8 flex items-center gap-3 md:hidden">
              <img src="/brand/yoreni_mark.svg" alt="" draggable={false} className="h-11 w-11 rounded-xl" />
              <span className="display-serif text-[1.9rem] leading-none">yoreni</span>
            </div>
            <h1 id="webui-auth-title" className="display-serif text-[2rem] leading-[1.1] text-foreground sm:text-[2.25rem]">
              {t("app.auth.title")}
            </h1>
            <form onSubmit={handleSubmit} className="mt-8">
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

export default function App() {
  useAppViewport();
  useReadingTypography();
  const [ready, setReady] = useState<Ready | null>(null);
  const [auth, setAuth] = useState(false);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const secret = useRef("");
  const clientRef = useRef<YoreniClient | null>(null);
  const generation = useRef(0);
  const refresh = useCallback(async () => {
    const boot = await fetchBootstrap("", secret.current);
    const contract = boot.terminal?.webui;
    if (!contract || contract.min_protocol > 2 || contract.max_protocol < 2 || !contract.capabilities?.includes("webui.core.v2")) throw new Error("WebUI protocol 2 is required");
    return boot;
  }, []);
  const start = useCallback(async (value: string) => {
    const attempt = ++generation.current;
    clientRef.current?.close(); clientRef.current = null;
    secret.current = value; setAuth(false); setError(null); setReady(null);
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
  if (auth) return <AuthScreen failed={failed} onSecret={(value) => { void start(value); }} />;
  if (error) return <BootError error={error} onRetry={() => { void start(secret.current); }} />;
  if (!ready) return <StartupShell />;
  return <ClientProvider client={ready.client} token={ready.boot.api_token ?? ""} modelName={ready.boot.model_name ?? null} ingressLimits={ready.boot.limits ?? null} webuiCapabilities={ready.boot.terminal?.webui?.capabilities ?? []}>
    <HashRouter>
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

function ChatView({ greeting, run, model, onOpenSettings }: {
  greeting: string;
  run: ReturnType<typeof useAgentRun>;
  model: string | null;
  onOpenSettings?: () => void;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const running = run.active || run.sending;
  const loadingHistory = !run.historyLoaded && run.messages.length === 0;
  const empty = run.messages.length === 0 && !loadingHistory && !running;
  useEffect(() => { if (following.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight; }, [run.messages, running]);
  return (
    <main className="relative flex min-h-0 flex-1 flex-col">
      <AgentRecovery />
      <div className={cn("flex min-h-0 flex-1 flex-col", empty && "justify-center")}>
        {loadingHistory ? (
          <HistorySkeleton />
        ) : empty ? (
          <div className="px-5 pb-8 pt-4 sm:pb-12">
            <h1 className="message-enter display-serif mx-auto max-w-[38rem] text-balance text-center text-[clamp(2.1rem,5vw,3.5rem)] leading-[1.04] text-foreground/90">
              {greeting}
            </h1>
          </div>
        ) : (
          <div
            ref={viewport}
            onScroll={() => {
              const element = viewport.current;
              if (element) following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
            }}
            className="thread-scroll min-h-0 flex-1 overflow-y-auto overscroll-contain"
          >
            <div data-testid="thread-message-region" className="mx-auto flex min-h-full w-full max-w-[44rem] flex-col justify-end gap-6 px-5 pb-10 pt-10 sm:px-6 sm:pb-12">
              {run.messages.map((message) => <MessageBubble key={message.id} message={message} />)}
              {running ? <AgentRunIndicator key="agent-run" className="-mt-3" /> : null}
            </div>
          </div>
        )}
        <div className={cn("shrink-0", empty ? "px-5 pb-8 sm:pb-10" : "px-4 pb-4 sm:px-6 sm:pb-6")}>
          <div className={cn("message-enter mx-auto w-full [animation-delay:90ms]", empty ? "max-w-[42rem]" : "max-w-[44rem]")}>
            {run.error ? (
              <p role="alert" className="mx-auto mb-2 w-fit rounded-full bg-destructive/10 px-3 py-1.5 text-[12px] text-destructive">
                {run.error}
              </p>
            ) : null}
            <ThreadComposer
              onSend={(content, attachments) => run.send(content, attachments?.map((item) => item.media))}
              onStop={() => { void run.stop(); }}
              isStreaming={run.active}
              variant={empty ? "hero" : "thread"}
              modelLabel={model}
              onModelBadgeClick={onOpenSettings}
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
  const { client, modelName } = useClient();
  const [model, setModel] = useState<string | null>(modelName ?? null);
  const [homeView, setHomeView] = useState<"chat" | "home">("chat");
  const run = useAgentRun();
  const greeting = useMemo(() => {
    const key = GREETINGS[Math.floor(Math.random() * GREETINGS.length)];
    return t(`thread.empty.greetings.${key}`);
  }, [t]);
  useEffect(() => client.onRuntimeModelUpdate(setModel), [client]);
  const location = useLocation();
  const homeOpen = location.pathname === "/" && homeView === "home";
  return <ThemeControlProvider control={control}>
    <AmbientBackground />
    <div className="relative z-10 flex h-full min-h-0 w-full text-foreground">
      <HomePanel open={homeOpen} />
      <div className={cn("relative flex min-h-0 flex-1 flex-col", homeOpen && "hidden md:flex")}>
      <div className="h-16 shrink-0" aria-hidden />
      <div className="app-route-view flex min-h-0 flex-1 flex-col">
      <Routes>
        <Route path="/" element={<ChatView greeting={greeting} run={run} model={model} onOpenSettings={() => navigate("/settings", { viewTransition: true })} />} />
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
            onStartAutomationChat={(text) => { setHomeView("chat"); navigate("/", { viewTransition: true }); void run.send(text); return true; }}
            onBackToChat={() => { setHomeView("chat"); navigate("/", { viewTransition: true }); }}
          />
        } />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      </div>
      </div>
      <AppHeader
        theme={theme}
        onToggleTheme={toggle}
        homeView={homeView}
        onHomeViewChange={setHomeView}
      />
    </div>
  </ThemeControlProvider>;
}
