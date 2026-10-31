import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Fingerprint, Loader2, Plus, Trash2 } from "lucide-react";

import { SettingsGroup, SettingsSectionTitle } from "@/components/settings/shared/SettingsControls";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  changeLoginPassword,
  fetchProfileSecurity,
  registerAuthenticator,
  removeAuthenticator,
  requestAuthenticatorRegistration,
} from "@/lib/api";
import type { AuthenticatorSummary, ProfileSecurity } from "@/lib/types";
import { PasskeyError, createPasskey, passkeysSupported } from "@/lib/webauthn";
import { useClient } from "@/providers/ClientProvider";

const PASSKEYS_CAPABILITY = "webui.passkeys.v1";
const PASSWORD_MIN = 8;

function errorText(reason: unknown, fallback: string): string {
  return reason instanceof Error && reason.message ? reason.message : fallback;
}

export function SecuritySettings() {
  const { t } = useTranslation();
  const { client, getToken, webuiCapabilities, onAuthSecretChange } = useClient();
  const [security, setSecurity] = useState<ProfileSecurity | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [passwordSaving, setPasswordSaving] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);
  const [passwordSaved, setPasswordSaved] = useState(false);

  const [passkeyName, setPasskeyName] = useState("");
  const [adding, setAdding] = useState(false);
  const [passkeyError, setPasskeyError] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);

  const supported = passkeysSupported();
  const available = webuiCapabilities.includes(PASSKEYS_CAPABILITY);

  useEffect(() => {
    if (!available) return;
    let live = true;
    fetchProfileSecurity(getToken()).then(
      (payload) => {
        if (!live) return;
        setSecurity(payload);
        setLoading(false);
      },
      (reason: unknown) => {
        if (!live) return;
        setLoadError(errorText(reason, t("profile.security.errors.loadFailed")));
        setLoading(false);
      },
    );
    return () => { live = false; };
  }, [available, getToken, t]);

  if (!available) return null;

  const canSavePassword = Boolean(current && next && confirm) && !passwordSaving;

  const savePassword = async () => {
    if (!canSavePassword) return;
    const secret = next.trim();
    if (secret.length < PASSWORD_MIN) {
      setPasswordError(t("profile.security.errors.passwordTooShort", { count: PASSWORD_MIN }));
      return;
    }
    if (secret !== confirm.trim()) {
      setPasswordError(t("profile.security.errors.passwordMismatch"));
      return;
    }
    setPasswordSaving(true);
    setPasswordError(null);
    try {
      await changeLoginPassword(client, current.trim(), secret);
      onAuthSecretChange?.(secret);
      setCurrent("");
      setNext("");
      setConfirm("");
      setPasswordSaved(true);
      window.setTimeout(() => setPasswordSaved(false), 2400);
    } catch (reason) {
      setPasswordError(errorText(reason, t("profile.security.errors.currentPasswordWrong")));
    } finally {
      setPasswordSaving(false);
    }
  };

  const addAuthenticator = async () => {
    if (adding) return;
    setAdding(true);
    setPasskeyError(null);
    try {
      const name = passkeyName.trim() || t("profile.security.authenticators.defaultName");
      const options = await requestAuthenticatorRegistration(client, name);
      const payload = await createPasskey(options, name);
      if (!payload) return;
      const result = await registerAuthenticator(client, payload);
      setSecurity((previous) => previous
        ? { ...previous, authenticators: [...previous.authenticators, result.authenticator] }
        : previous);
      setPasskeyName("");
    } catch (reason) {
      if (reason instanceof PasskeyError && reason.code === "already-registered") {
        setPasskeyError(t("profile.security.errors.passkeyExists"));
      } else if (reason instanceof PasskeyError) {
        setPasskeyError(t("profile.security.authenticators.secureContext"));
      } else {
        setPasskeyError(errorText(reason, t("profile.security.errors.passkeyFailed")));
      }
    } finally {
      setAdding(false);
    }
  };

  const removeOne = async (authenticator: AuthenticatorSummary) => {
    if (removingId) return;
    setRemovingId(authenticator.id);
    setPasskeyError(null);
    try {
      setSecurity(await removeAuthenticator(client, authenticator.id));
    } catch (reason) {
      setPasskeyError(errorText(reason, t("profile.security.errors.passkeyFailed")));
    } finally {
      setRemovingId(null);
    }
  };

  const formatDate = (value: string | null) => {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString();
  };

  return (
    <>
      <section>
        <SettingsSectionTitle>{t("profile.security.password.title")}</SettingsSectionTitle>
        <SettingsGroup>
          <div className="space-y-4 p-5 sm:p-6">
            <p className="text-[12.5px] leading-5 text-muted-foreground">
              {t("profile.security.password.hint")}
            </p>
            <Input
              type="password"
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
              autoComplete="current-password"
              aria-label={t("profile.security.password.current")}
              placeholder={t("profile.security.password.current")}
              className="h-11 rounded-full border-white/55 bg-white/45 px-5 text-[14px] backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.06]"
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <Input
                type="password"
                value={next}
                onChange={(event) => setNext(event.target.value)}
                autoComplete="new-password"
                aria-label={t("profile.security.password.new")}
                placeholder={t("profile.security.password.new")}
                className="h-11 rounded-full border-white/55 bg-white/45 px-5 text-[14px] backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.06]"
              />
              <Input
                type="password"
                value={confirm}
                onChange={(event) => setConfirm(event.target.value)}
                autoComplete="new-password"
                aria-label={t("profile.security.password.confirm")}
                placeholder={t("profile.security.password.confirm")}
                className="h-11 rounded-full border-white/55 bg-white/45 px-5 text-[14px] backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.06]"
              />
            </div>
            {passwordError ? (
              <p role="alert" className="text-[12.5px] leading-5 text-destructive">{passwordError}</p>
            ) : null}
            <div className="flex items-center justify-end gap-3">
              {passwordSaved ? (
                <span className="inline-flex items-center gap-1.5 text-[12.5px] text-emerald-700 dark:text-emerald-400">
                  <Check className="h-3.5 w-3.5" aria-hidden />
                  {t("profile.security.password.saved")}
                </span>
              ) : null}
              <Button
                onClick={() => { void savePassword(); }}
                disabled={!canSavePassword}
                className="h-10 rounded-full px-5"
              >
                {passwordSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden /> : null}
                {t("profile.security.password.save")}
              </Button>
            </div>
          </div>
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>{t("profile.security.authenticators.title")}</SettingsSectionTitle>
        <SettingsGroup>
          <div className="space-y-4 p-5 sm:p-6">
            <p className="text-[12.5px] leading-5 text-muted-foreground">
              {t("profile.security.authenticators.hint")}
            </p>

            {loading ? (
              <div className="flex h-12 items-center justify-center text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              </div>
            ) : loadError ? (
              <p role="alert" className="text-[12.5px] leading-5 text-destructive">{loadError}</p>
            ) : security && security.authenticators.length > 0 ? (
              <ul className="divide-y divide-foreground/8 dark:divide-white/10">
                {security.authenticators.map((authenticator) => {
                  const created = formatDate(authenticator.created_at);
                  return (
                    <li key={authenticator.id} className="flex items-center gap-3 py-3 first:pt-0 last:pb-0">
                      <Fingerprint className="h-4 w-4 shrink-0 text-muted-foreground" strokeWidth={1.75} aria-hidden />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-[13.5px] text-foreground">{authenticator.name}</p>
                        <p className="text-[11.5px] text-muted-foreground/80">
                          {authenticator.last_used_at
                            ? t("profile.security.authenticators.lastUsed", { date: formatDate(authenticator.last_used_at) })
                            : created
                              ? t("profile.security.authenticators.addedOn", { date: created })
                              : null}
                        </p>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={t("profile.security.authenticators.remove", { name: authenticator.name })}
                        title={t("profile.security.authenticators.remove", { name: authenticator.name })}
                        disabled={removingId === authenticator.id}
                        onClick={() => { void removeOne(authenticator); }}
                        className="h-9 w-9 shrink-0 rounded-full text-muted-foreground hover:text-destructive"
                      >
                        {removingId === authenticator.id
                          ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                          : <Trash2 className="h-4 w-4" strokeWidth={1.75} aria-hidden />}
                      </Button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-[12.5px] leading-5 text-muted-foreground">
                {t("profile.security.authenticators.empty")}
              </p>
            )}

            {passkeyError ? (
              <p role="alert" className="text-[12.5px] leading-5 text-destructive">{passkeyError}</p>
            ) : null}

            {supported ? (
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
                <Input
                  value={passkeyName}
                  onChange={(event) => setPasskeyName(event.target.value)}
                  maxLength={64}
                  aria-label={t("profile.security.authenticators.nameLabel")}
                  placeholder={t("profile.security.authenticators.namePlaceholder")}
                  className="h-11 rounded-full border-white/55 bg-white/45 px-5 text-[14px] backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.06]"
                />
                <Button
                  onClick={() => { void addAuthenticator(); }}
                  disabled={adding || loading || Boolean(loadError)}
                  className="h-11 shrink-0 rounded-full px-5"
                >
                  {adding
                    ? <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
                    : <Plus className="mr-2 h-4 w-4" strokeWidth={1.75} aria-hidden />}
                  {t("profile.security.authenticators.add")}
                </Button>
              </div>
            ) : (
              <p className="text-[12.5px] leading-5 text-muted-foreground">
                {t("profile.security.authenticators.secureContext")}
              </p>
            )}
          </div>
        </SettingsGroup>
      </section>
    </>
  );
}
