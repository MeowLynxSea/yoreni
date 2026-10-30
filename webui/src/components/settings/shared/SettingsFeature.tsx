import { useEffect, useRef, useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ToggleButton } from "@/components/ui/toggle-button";
import { SettingsGroup, SettingsRow } from "@/components/settings/shared/SettingsControls";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Disclosure } from "@/components/ui/disclosure";

export function SettingsAdvancedOptions({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <Disclosure
      summaryClassName="settings-list-inset flex min-h-12 cursor-pointer items-center justify-between gap-4 rounded-control text-[13px] leading-5 text-muted-foreground settings-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      summary={<>
        {t("settings.runtimeConfig.advancedOptions")}
        <ChevronDown aria-hidden className="h-3.5 w-3.5 shrink-0 transition-transform duration-200 group-data-[state=open]/disclosure:rotate-180 motion-reduce:transition-none" />
      </>}
    >
      <div className="space-y-1">{children}</div>
    </Disclosure>
  );
}

export function SettingsFeature({
  title, description, enabled, onChange, disabled, initialOpen = false, error, children, onBeforeClose,
}: {
  title: string;
  description?: string;
  enabled: boolean;
  onChange: (enabled: boolean) => void;
  disabled?: boolean;
  initialOpen?: boolean;
  error?: string;
  children?: ReactNode;
  onBeforeClose?: () => Promise<boolean>;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(initialOpen && enabled);
  const section = useRef<HTMLElement>(null);
  useEffect(() => {
    if (initialOpen && enabled) {
      setOpen(true);
      section.current?.scrollIntoView?.({ block: "nearest" });
    }
  }, [initialOpen, enabled]);
  return (
    <section ref={section} aria-label={title}>
      <Dialog open={Boolean(children) && open} onOpenChange={(next) => {
        if (!next && onBeforeClose) {
          void onBeforeClose().then((saved) => { if (saved) setOpen(false); });
        } else setOpen(next);
      }}>
      <SettingsGroup>
        <SettingsRow title={children ? (
          <DialogTrigger asChild>
          <button type="button"
            className="flex min-h-9 w-full items-center rounded-full text-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            {title}
          </button>
          </DialogTrigger>
        ) : title}>
          {children ? <DialogTrigger asChild>
            <button type="button"
              className="mr-3 shrink-0 rounded-full px-2.5 py-1 text-[13px] leading-5 text-muted-foreground settings-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              {t("settings.configure")}
            </button>
          </DialogTrigger> : null}
          <ToggleButton checked={enabled} disabled={disabled} ariaLabel={title} label={title}
            onChange={(next) => { setOpen(next); onChange(next); }} />
        </SettingsRow>
      </SettingsGroup>
      {children ? <DialogContent aria-describedby={undefined} className="max-h-[85dvh] w-[min(calc(100vw-2rem),40rem)] max-w-none gap-0 overflow-y-auto p-0">
        <DialogHeader className="px-6 pb-3 pr-12 pt-6 text-left">
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription className="leading-5">{description}</DialogDescription> : null}
        </DialogHeader>
        <div className="pb-4">{children}</div>
      </DialogContent> : null}
      </Dialog>
      {error && !open ? <p role="alert" className="settings-list-inset pt-2 text-[13px] leading-5 text-destructive">{error}</p> : null}
    </section>
  );
}
