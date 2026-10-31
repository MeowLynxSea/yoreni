import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Loader2 } from "lucide-react";

import { BirthdayField } from "@/components/profile/BirthdayField";
import { ChatStyleEditor } from "@/components/profile/ChatStyleEditor";
import { SettingsGroup, SettingsSectionTitle } from "@/components/settings/shared/SettingsControls";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { fetchUserProfile, updateUserProfile } from "@/lib/api";
import { isProfileComplete, profileFromWire } from "@/lib/onboarding";
import type { UserProfile } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

const GENDERS = ["male", "female", "undisclosed"] as const;

export function ProfileSettings() {
  const { t } = useTranslation();
  const { client, getToken } = useClient();
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [saved, setSaved] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState(false);

  useEffect(() => {
    let live = true;
    fetchUserProfile(getToken()).then(
      (payload) => {
        if (!live) return;
        const next = profileFromWire(payload.profile);
        setProfile(next);
        setSaved(next);
        setLoading(false);
      },
      (reason: unknown) => {
        if (!live) return;
        setError(reason instanceof Error ? reason.message : String(reason));
        setLoading(false);
      },
    );
    return () => { live = false; };
  }, [getToken]);

  const dirty = useMemo(() => {
    if (!profile || !saved) return false;
    return JSON.stringify(profile) !== JSON.stringify(saved);
  }, [profile, saved]);

  const update = (patch: Partial<UserProfile>) =>
    setProfile((current) => (current ? { ...current, ...patch } : current));

  const save = async () => {
    if (!profile || saving) return;
    if (!profile.name.trim()) {
      setError(t("profile.errors.nameRequired"));
      return;
    }
    if (profile.style.base === "custom" && !profile.style.custom.trim()) {
      setError(t("profile.errors.customRequired"));
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload = await updateUserProfile(client, profile);
      const next = profileFromWire(payload.profile);
      setProfile(next);
      setSaved(next);
      setNotice(true);
      window.setTimeout(() => setNotice(false), 2400);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="settings-stack">
        <div className="settings-sheet flex h-40 items-center justify-center text-muted-foreground">
          <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
        </div>
      </div>
    );
  }

  if (!profile) {
    return (
      <p role="alert" className="settings-list-inset text-[13px] text-destructive">
        {error ?? t("profile.loadFailed")}
      </p>
    );
  }

  return (
    <div className="settings-stack">
      <p className="settings-list-inset max-w-[44rem] text-[13px] leading-6 text-muted-foreground">
        {t("profile.intro")}
      </p>

      <section>
        <SettingsSectionTitle>{t("profile.sections.identity")}</SettingsSectionTitle>
        <SettingsGroup>
          <div className="space-y-5 p-5 sm:p-6">
            <div>
              <label className="mb-2 block text-[13px] font-medium text-foreground/85" htmlFor="profile-name">
                {t("profile.name")}
              </label>
              <Input
                id="profile-name"
                value={profile.name}
                onChange={(event) => update({ name: event.target.value })}
                maxLength={80}
                autoComplete="nickname"
                className="h-11 rounded-full border-white/55 bg-white/45 px-5 text-[14px] backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.06]"
              />
              <p className="mt-1.5 text-[11.5px] text-muted-foreground/75">{t("profile.nameHint")}</p>
            </div>

            <div>
              <p className="mb-2 text-[13px] font-medium text-foreground/85">{t("profile.birthday")}</p>
              <BirthdayField
                value={profile.birthday}
                onChange={(birthday) => update({ birthday })}
                idPrefix="profile-birthday"
              />
            </div>

            <div>
              <p className="mb-2 text-[13px] font-medium text-foreground/85">{t("profile.gender.label")}</p>
              <div className="flex flex-wrap gap-2">
                {GENDERS.map((gender) => (
                  <button
                    key={gender}
                    type="button"
                    onClick={() => update({ gender: profile.gender === gender ? null : gender })}
                    className={
                      profile.gender === gender
                        ? "rounded-full bg-foreground px-4 py-2 text-[13px] text-background"
                        : "rounded-full border border-foreground/12 bg-white/40 px-4 py-2 text-[13px] text-foreground/80 transition-colors hover:border-foreground/25 dark:border-white/10 dark:bg-white/[0.05]"
                    }
                  >
                    {t(`profile.gender.${gender}`)}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>{t("profile.style.sectionTitle")}</SettingsSectionTitle>
        <SettingsGroup>
          <div className="p-5 sm:p-6">
            <ChatStyleEditor
              value={profile.style}
              onChange={(style) => update({ style })}
              idPrefix="profile-settings-style"
            />
          </div>
        </SettingsGroup>
      </section>

      <section>
        <SettingsSectionTitle>{t("profile.sections.notes")}</SettingsSectionTitle>
        <SettingsGroup>
          <div className="p-5 sm:p-6">
            <Textarea
              id="profile-notes"
              value={profile.notes}
              onChange={(event) => update({ notes: event.target.value })}
              rows={4}
              maxLength={2000}
              aria-label={t("profile.sections.notes")}
              placeholder={t("profile.notesPlaceholder")}
              className="rounded-2xl border-white/55 bg-white/45 px-5 py-4 text-[14px] leading-6 backdrop-blur-xl dark:border-white/10 dark:bg-white/[0.06]"
            />
          </div>
        </SettingsGroup>
      </section>

      {error ? (
        <p role="alert" className="settings-list-inset text-[12.5px] leading-5 text-destructive">
          {error}
        </p>
      ) : null}

      <div className="settings-footer flex flex-wrap items-center justify-end gap-3">
        {notice ? (
          <span className="inline-flex items-center gap-1.5 text-[12.5px] text-emerald-700 dark:text-emerald-400">
            <Check className="h-3.5 w-3.5" aria-hidden />
            {t("profile.saved")}
          </span>
        ) : null}
        <Button
          onClick={() => void save()}
          disabled={!dirty || saving || !isProfileComplete(profile)}
          className="h-11 rounded-full px-6"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
          {t("profile.save")}
        </Button>
      </div>
    </div>
  );
}
