import { useState } from "react";
import { useTranslation } from "react-i18next";

import { currentLocale, setAppLanguage } from "@/i18n";
import { supportedLocales, type SupportedLocale } from "@/i18n/config";
import { cn } from "@/lib/utils";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

export function LanguageSwitcher({ className, collapsed = false }: { className?: string; collapsed?: boolean }) {
  const { t } = useTranslation();
  const [pointerFocus, setPointerFocus] = useState(false);
  const locale = currentLocale();
  const compactLabel = locale === "zh-CN"
    ? "中"
    : locale === "zh-TW"
      ? "繁"
      : locale.split("-")[0].toUpperCase();
  return (
    <Select value={currentLocale()} onValueChange={(value) => { void setAppLanguage(value as SupportedLocale); }}>
      <SelectTrigger
        className={cn(
          collapsed
            ? "h-9 w-9 min-w-0 justify-center gap-0 rounded-full px-0 [&>svg]:hidden"
            : "min-w-40 rounded-full",
          pointerFocus && "focus-visible:ring-0",
          className,
        )}
        aria-label={t("sidebar.language.ariaLabel")}
        title={t("sidebar.language.ariaLabel")}
        onPointerDown={() => setPointerFocus(true)}
        onKeyDown={() => setPointerFocus(false)}
        onBlur={() => setPointerFocus(false)}
      >
        {collapsed ? <span className="text-[11px] font-semibold uppercase leading-none">{compactLabel}</span> : <SelectValue />}
      </SelectTrigger>
      <SelectContent
        onPointerUpCapture={() => setPointerFocus(true)}
        onPointerDownOutside={() => setPointerFocus(true)}
        onKeyDownCapture={() => setPointerFocus(false)}
        onEscapeKeyDown={() => setPointerFocus(false)}
      >
        {supportedLocales.map((option) => (
          <SelectItem key={option.code} value={option.code}>{option.nativeLabel}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
