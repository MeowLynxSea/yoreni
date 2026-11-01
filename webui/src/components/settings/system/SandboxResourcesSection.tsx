import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  NumberInput,
  SettingsGroup,
  SettingsRow,
  SettingsSectionTitle,
} from "@/components/settings/shared/SettingsControls";
import { fetchSandboxStatus, rebuildSandbox, updateSandboxResources } from "@/lib/api";
import { markSandboxRebuild } from "@/lib/sandbox-rebuild";
import type { SandboxResources } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

/**
 * The VM's CPU count and memory. Both are creation options, so a saved value
 * only takes effect once the virtual machine is rebuilt.
 */
export function SandboxResourcesSection() {
  const { t } = useTranslation();
  const { client, getToken } = useClient();
  const [saved, setSaved] = useState<SandboxResources | null>(null);
  const [cpus, setCpus] = useState(2);
  const [memoryGb, setMemoryGb] = useState(4);
  const [saving, setSaving] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [needsRebuild, setNeedsRebuild] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const token = getToken();
    if (!token) return;
    void fetchSandboxStatus(token)
      .then((status) => {
        if (!live) return;
        setSaved(status.resources);
        setCpus(status.resources.cpus);
        setMemoryGb(status.resources.memory_mib / 1024);
      })
      .catch(() => {
        // An instance without a VM has nothing to size.
      });
    return () => {
      live = false;
    };
  }, [getToken]);

  if (!saved) return null;

  const changed = cpus !== saved.cpus || memoryGb * 1024 !== saved.memory_mib;

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const next = await updateSandboxResources(client, { cpus, memory_mib: memoryGb * 1024 });
      setSaved(next);
      setCpus(next.cpus);
      setMemoryGb(next.memory_mib / 1024);
      setNeedsRebuild(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  const rebuild = async () => {
    setConfirming(false);
    setRebuilding(true);
    setError(null);
    try {
      await rebuildSandbox(client);
      setNeedsRebuild(false);
      // The overlay takes over from here and hides the interface until the VM
      // is back.
      markSandboxRebuild(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRebuilding(false);
    }
  };

  return (
    <div className="settings-stack">
      <SettingsSectionTitle>{t("settings.sandbox.title")}</SettingsSectionTitle>
      <SettingsGroup>
        <SettingsRow
          title={t("settings.sandbox.cpus")}
          description={t("settings.sandbox.description")}
        >
          <NumberInput
            value={cpus}
            min={1}
            max={saved.cpus_max}
            ariaLabel={t("settings.sandbox.cpus")}
            onChange={(value) => {
              if (Number.isFinite(value)) setCpus(Math.round(value));
            }}
          />
        </SettingsRow>
        <SettingsRow title={t("settings.sandbox.memory")}>
          <NumberInput
            value={memoryGb}
            min={1}
            max={saved.memory_mib_max / 1024}
            suffix="GB"
            ariaLabel={t("settings.sandbox.memory")}
            onChange={(value) => {
              if (Number.isFinite(value)) setMemoryGb(Math.round(value));
            }}
          />
        </SettingsRow>
      </SettingsGroup>
      <p className="px-1 text-[12px] leading-5 text-muted-foreground">
        {t("settings.sandbox.limit", {
          cpus: saved.cpus_max,
          memory: saved.memory_mib_max / 1024,
        })}
      </p>
      {changed || needsRebuild ? (
        <div className="rounded-2xl border border-border/60 bg-foreground/[0.03] px-4 py-3 text-[12px] leading-5 text-muted-foreground">
          {t("settings.sandbox.note")}
        </div>
      ) : null}
      {error ? (
        <p className="px-1 text-[12px] leading-5 text-destructive">{error}</p>
      ) : null}
      <div className="flex flex-wrap items-center gap-2 px-1">
        <Button
          type="button"
          onClick={() => void save()}
          disabled={!changed || saving}
          className="h-10 px-5"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
          <span>{t(saving ? "settings.actions.saving" : "settings.actions.save")}</span>
        </Button>
        <Button
          type="button"
          variant="outline"
          onClick={() => setConfirming(true)}
          disabled={rebuilding || changed}
          className="h-10 px-5"
        >
          {rebuilding ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
          <span>{t("settings.sandbox.rebuild")}</span>
        </Button>
      </div>
      {confirming ? (
        <AlertDialog open onOpenChange={(open) => { if (!open) setConfirming(false); }}>
          <AlertDialogContent className="w-[min(calc(100vw-2rem),26rem)] gap-0 p-5">
            <AlertDialogHeader className="space-y-0 text-left">
              <AlertDialogTitle className="text-[18px] font-semibold leading-tight tracking-[-0.02em] text-foreground">
                {t("settings.sandbox.rebuildTitle")}
              </AlertDialogTitle>
              <AlertDialogDescription className="mt-2 text-[13.5px] leading-6 text-muted-foreground">
                {t("settings.sandbox.rebuildBody")}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter className="mt-5 !grid grid-cols-1 gap-2 sm:grid-cols-2">
              <AlertDialogCancel onClick={() => setConfirming(false)} className="mt-0 w-full min-w-0">
                {t("settings.actions.cancel")}
              </AlertDialogCancel>
              <AlertDialogAction
                onClick={() => void rebuild()}
                className="w-full min-w-0 !whitespace-normal text-center"
              >
                {t("settings.sandbox.rebuildConfirm")}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      ) : null}
    </div>
  );
}
