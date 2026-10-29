import { cn } from "@/lib/utils";
import { Disclosure } from "@/components/ui/disclosure";
import { ChevronDown } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowRight, Eye, EyeOff, Settings, MessageCircle, LogOut, PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";
import { StartupShell } from "@/components/StartupShell";
import { AgentRecovery } from "@/components/AgentRecovery";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { MessageBubble } from "@/components/MessageBubble";
import { ThreadComposer } from "@/components/thread/ThreadComposer";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ThemeToggleIcon } from "@/components/icons/ThemeToggleIcon";
import { useAgentRun } from "@/hooks/useAgentRun";
import { ThemeProvider, useTheme } from "@/hooks/useTheme";
import { useAppViewport } from "@/hooks/useAppViewport";
import { useReadingTypography } from "@/hooks/useReadingTypography";
import { BootstrapAuthRequiredError, clearSavedSecret, consumeUrlBootstrapSecret, deriveWsUrl, fetchBootstrap, loadSavedSecret, saveSecret } from "@/lib/bootstrap";
import { createRuntimeHost } from "@/lib/runtime";
import { YoreniClient } from "@/lib/yoreni-client";
import type { BootstrapResponse } from "@/lib/types";
import { ClientProvider, useClient } from "@/providers/ClientProvider";

const SettingsView = lazy(() => import("@/components/settings/SettingsView").then((module) => ({ default: module.SettingsView })));
type Ready = { client: YoreniClient; boot: BootstrapResponse };
const SIDEBAR_COLLAPSED_KEY = "yoreni-webui.sidebar.collapsed";

function readSidebarCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "true";
  } catch {
    return false;
  }
}

function AgentNavButton({
  collapsed,
  label,
  icon,
  onClick,
}: {
  collapsed: boolean;
  label: string;
  icon: ReactNode;
  onClick: () => void;
}) {
  return (
    <Button
      variant="ghost"
      aria-label={label}
      title={collapsed ? label : undefined}
      onClick={onClick}
      className={cn(
        "h-10 shrink-0",
        collapsed ? "w-10 justify-center px-0" : "mx-2 justify-start gap-3 px-3",
      )}
    >
      {icon}
      {!collapsed && <span>{label}</span>}
    </Button>
  );
}

function AuthForm({
  failed,
  onSecret,
}: {
  failed: boolean;
  onSecret: (secret: string) => void;
}) {
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

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
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
    <main className="flex h-full w-full flex-col overflow-y-auto bg-background">
      <div className="mx-5 mt-5 shrink-0 self-end sm:mx-8">
        <LanguageSwitcher className="h-8 min-w-0 gap-2 px-3 text-xs" />
      </div>
      <div className="flex flex-1 shrink-0 items-center justify-center px-6 pb-20 pt-8">
        <section
          aria-labelledby="webui-auth-title"
          className="w-full max-w-xs"
        >
          <div className="text-center">
            <img
              src="/brand/yoreni_mark.svg"
              alt=""
              width={56}
              height={56}
              draggable={false}
              className="mx-auto mb-5 h-14 w-14 select-none"
            />
            <h1 id="webui-auth-title" className="text-balance text-2xl font-semibold tracking-tight text-foreground sm:text-[1.75rem]">
              {t("app.auth.title")}
            </h1>
          </div>
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
                onChange={(e) => {
                  setValue(e.target.value);
                  setValidationError(null);
                }}
                disabled={submitting}
                aria-invalid={validationError ? true : undefined}
                aria-describedby={validationError ? "webui-auth-error" : undefined}
                placeholder={errorMessage ?? undefined}
                className={cn(
                  "h-12 rounded-full border-foreground/15 bg-muted/30 px-4 pr-24 text-base",
                  validationError && "placeholder:text-[13px] placeholder:text-red-600 dark:placeholder:text-red-400",
                )}
                autoFocus
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                disabled={submitting}
                aria-label={t(
                  passwordVisible ? "app.auth.hidePassword" : "app.auth.showPassword",
                )}
                aria-controls="webui-access-password"
                onClick={() => setPasswordVisible((visible) => !visible)}
                className="absolute right-12 top-1/2 h-10 w-10 -translate-y-1/2 rounded-full text-muted-foreground hover:text-foreground"
              >
                {passwordVisible ? (
                  <EyeOff className="h-4 w-4" strokeWidth={1.75} aria-hidden />
                ) : (
                  <Eye className="h-4 w-4" strokeWidth={1.75} aria-hidden />
                )}
              </Button>
              <Button
                type="submit"
                variant="ghost"
                size="icon"
                disabled={submitting}
                aria-label={t("app.auth.submit")}
                title={t("app.auth.submit")}
                className="absolute right-1 top-1/2 h-10 w-10 -translate-y-1/2 rounded-full"
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
            className="mt-4"
            summaryClassName="flex min-h-11 items-center justify-center gap-1.5 rounded-compact text-[13px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
            contentClassName="rounded-control bg-muted/50 p-4 text-sm leading-6 text-muted-foreground"
            summary={(
              <>
                <span>{t("app.auth.helpTitle")}</span>
                <ChevronDown className="h-3.5 w-3.5 shrink-0 transition-transform group-data-[state=open]/disclosure:rotate-180 motion-reduce:transition-none" strokeWidth={1.5} aria-hidden />
              </>
            )}
          >
            <p>{t("app.auth.helpConfig")}</p>
            <code className="mt-2 block rounded-compact bg-background px-3 py-2 font-mono text-xs text-foreground [overflow-wrap:anywhere]">
              ~/.yoreni/config.json
            </code>
            <p className="mt-4">{t("app.auth.helpSecret")}</p>
            <code className="mt-2 block rounded-compact bg-background px-3 py-2 font-mono text-xs text-foreground [overflow-wrap:anywhere]">
              channels.websocket.tokenIssueSecret
            </code>
            <p className="mt-3">
              <Trans
                i18nKey="app.auth.helpFallback"
                components={{ code: <code className="font-mono text-xs text-foreground [overflow-wrap:anywhere]" /> }}
              />
            </p>
          </Disclosure>
        </section>
      </div>
    </main>
  );
}


export default function App() {
  useAppViewport();
  useReadingTypography();
  const { t } = useTranslation();
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
  if (auth) return <AuthForm failed={failed} onSecret={(value) => { void start(value); }} />;
  if (error) return <div role="alert" className="m-auto max-w-lg p-8"><h1>{t("app.error.title")}</h1><p>{error}</p><Button onClick={() => { void start(secret.current); }}>{t("common.retry")}</Button></div>;
  if (!ready) return <StartupShell />;
  return <ClientProvider client={ready.client} token={ready.boot.api_token ?? ""} modelName={ready.boot.model_name ?? null} ingressLimits={ready.boot.limits ?? null} webuiCapabilities={ready.boot.terminal?.webui?.capabilities ?? []}>
    <AgentShell onLogout={() => { ++generation.current; ready.client.close(); clearSavedSecret(); setReady(null); setAuth(true); }} />
  </ClientProvider>;
}

function AgentShell({ onLogout }: { onLogout: () => void }) {
  const { t } = useTranslation();
  const { preference, setTheme, toggle, theme } = useTheme();
  const { client, modelName } = useClient();
  const [settings, setSettings] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readSidebarCollapsed);
  const [model, setModel] = useState(modelName);
  const run = useAgentRun();
  const viewport = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  useEffect(() => client.onRuntimeModelUpdate(setModel), [client]);
  useEffect(() => {
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, String(sidebarCollapsed));
    } catch {
      // Local persistence is optional; the control still works for this page.
    }
  }, [sidebarCollapsed]);
  useEffect(() => { if (following.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight; }, [run.messages]);
  return <ThemeProvider theme={theme}><div className="flex h-full min-h-0 w-full bg-background text-foreground">
    <nav aria-label={t("sidebar.navigation")} className={cn("flex shrink-0 flex-col gap-3 border-r border-border py-4 transition-[width] duration-200 motion-reduce:transition-none", sidebarCollapsed ? "w-14 items-center" : "w-60")}>
      <div className={cn("flex w-full items-center px-3", sidebarCollapsed ? "justify-center" : "justify-between px-4")}>
        <button
          type="button"
          aria-label={sidebarCollapsed ? t("thread.header.toggleSidebar") : "Yoreni"}
          title={sidebarCollapsed ? t("thread.header.toggleSidebar") : undefined}
          aria-hidden={!sidebarCollapsed}
          aria-expanded={!sidebarCollapsed}
          onClick={() => sidebarCollapsed && setSidebarCollapsed(false)}
          tabIndex={sidebarCollapsed ? 0 : -1}
          className={cn("flex h-9 shrink-0 items-center gap-2 rounded-control", sidebarCollapsed ? "w-9 justify-center" : "pointer-events-none")}
        >
          <img
            src={sidebarCollapsed ? "/brand/yoreni_mark.svg" : "/brand/yoreni_wordmark.svg"}
            alt=""
            className={cn("select-none object-contain", sidebarCollapsed ? "h-8 w-8" : "h-7 w-32")}
            draggable={false}
          />
        </button>
        {!sidebarCollapsed && <Button variant="ghost" size="icon" aria-label={t("sidebar.collapse")} title={t("sidebar.collapse")} aria-expanded="true" onClick={() => setSidebarCollapsed(true)}><PanelLeftClose className="h-4 w-4" /></Button>}
      </div>
      <div className={cn("flex flex-col gap-2", !sidebarCollapsed && "w-full")}>
        <AgentNavButton collapsed={sidebarCollapsed} label={t("settings.backToChat")} icon={<MessageCircle className="h-5 w-5" />} onClick={() => setSettings(false)} />
        <AgentNavButton collapsed={sidebarCollapsed} label={t("sidebar.settings")} icon={<Settings className="h-5 w-5" />} onClick={() => setSettings(true)} />
      </div>
      <div className={cn("mt-auto flex gap-2", sidebarCollapsed ? "flex-col items-center" : "w-full flex-col px-2")}>
        <LanguageSwitcher collapsed={sidebarCollapsed} className={sidebarCollapsed ? undefined : "w-full"} />
        <AgentNavButton collapsed={sidebarCollapsed} label={t("settings.nav.appearance")} icon={<ThemeToggleIcon theme={theme} />} onClick={toggle} />
        <AgentNavButton collapsed={sidebarCollapsed} label={t("settings.oauth.signOut")} icon={<LogOut className="h-4 w-4" />} onClick={onLogout} />
      </div>
      {sidebarCollapsed && <Button variant="ghost" size="icon" aria-label={t("thread.header.toggleSidebar")} title={t("thread.header.toggleSidebar")} aria-expanded="false" onClick={() => setSidebarCollapsed(false)}><PanelLeftOpen className="h-4 w-4" /></Button>}
    </nav>
    <main className="flex min-h-0 min-w-0 flex-1 flex-col">
      {settings ? <Suspense fallback={<StartupShell />}><SettingsView themePreference={preference} showSidebar onChangeTheme={setTheme} onBackToChat={() => setSettings(false)} onModelNameChange={setModel} onLogout={onLogout} onStartAutomationChat={(text) => { setSettings(false); void run.send(text); return true; }} /></Suspense> : <>
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-border px-5"><h1 className="font-semibold">Yoreni</h1><span className="text-xs text-muted-foreground">{model}</span></header>
        <AgentRecovery />
        <div ref={viewport} onScroll={() => { const element = viewport.current; if (element) following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80; }} className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 sm:px-8">
          <div className="mx-auto flex max-w-3xl flex-col gap-6 py-8">{run.messages.map((message) => <MessageBubble key={message.id} message={message} />)}</div>
        </div>
        {run.error && <p role="alert" className="mx-auto w-full max-w-3xl px-4 text-sm text-destructive">{run.error}</p>}
        <div className="mx-auto w-full max-w-3xl shrink-0 p-4"><ThreadComposer onSend={(content, attachments) => run.send(content, attachments?.map((item) => item.media))} onStop={() => { void run.stop(); }} isStreaming={run.active} /></div>
      </>}
    </main>
  </div></ThemeProvider>;
}
