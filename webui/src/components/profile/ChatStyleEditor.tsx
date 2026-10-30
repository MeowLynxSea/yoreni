import { useTranslation } from "react-i18next";

import { SegmentedControl } from "@/components/ui/segmented-control";
import { Textarea } from "@/components/ui/textarea";
import { CHAT_STYLE_BASES, STYLE_DIMENSIONS, STYLE_TUNINGS } from "@/lib/onboarding";
import type { ChatStyle, ChatStyleBase, StyleTuning } from "@/lib/types";
import { cn } from "@/lib/utils";

const BASE_DESCRIPTIONS: Record<ChatStyleBase, string> = {
  default: "profile.style.base.defaultDescription",
  professional: "profile.style.base.professionalDescription",
  friendly: "profile.style.base.friendlyDescription",
  direct: "profile.style.base.directDescription",
  imaginative: "profile.style.base.imaginativeDescription",
  pragmatic: "profile.style.base.pragmaticDescription",
  custom: "profile.style.base.customDescription",
};

const DIMENSION_DESCRIPTIONS: Record<(typeof STYLE_DIMENSIONS)[number], string> = {
  affability: "profile.style.dimension.affabilityDescription",
  enthusiasm: "profile.style.dimension.enthusiasmDescription",
  emoji: "profile.style.dimension.emojiDescription",
  structure: "profile.style.dimension.structureDescription",
};

export function ChatStyleEditor({
  value,
  onChange,
  idPrefix = "chat-style",
  className,
}: {
  value: ChatStyle;
  onChange: (next: ChatStyle) => void;
  idPrefix?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const update = (patch: Partial<ChatStyle>) => onChange({ ...value, ...patch });

  return (
    <div className={cn("space-y-7", className)}>
      <fieldset>
        <legend className="mb-3 text-[13px] font-medium text-foreground/80">
          {t("profile.style.baseLabel")}
        </legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {CHAT_STYLE_BASES.map((base) => {
            const selected = value.base === base;
            return (
              <label
                key={base}
                className={cn(
                  "group relative flex cursor-pointer select-none flex-col gap-1 rounded-2xl border px-4 py-3.5 transition-all duration-200",
                  "focus-within:outline-none focus-within:ring-2 focus-within:ring-ring/40",
                  selected
                    ? "border-foreground/25 bg-white/70 shadow-[0_18px_40px_-28px_rgba(20,32,48,0.55)] dark:border-white/25 dark:bg-white/[0.1]"
                    : "border-white/55 bg-white/30 hover:border-white/80 hover:bg-white/45 dark:border-white/10 dark:bg-white/[0.03] dark:hover:border-white/20 dark:hover:bg-white/[0.06]",
                )}
              >
                <input
                  type="radio"
                  name={`${idPrefix}-base`}
                  value={base}
                  checked={selected}
                  onChange={() => update({ base })}
                  className="sr-only"
                />
                <span className="flex items-center justify-between gap-2">
                  <span className="text-[14px] font-medium text-foreground/95">
                    {t(`profile.style.base.${base}`)}
                  </span>
                  <span
                    aria-hidden
                    className={cn(
                      "grid h-4 w-4 place-items-center rounded-full border transition-colors",
                      selected
                        ? "border-foreground/60 bg-foreground/85"
                        : "border-foreground/25 bg-transparent",
                    )}
                  >
                    <span
                      className={cn(
                        "h-1.5 w-1.5 rounded-full bg-background transition-opacity",
                        selected ? "opacity-100" : "opacity-0",
                      )}
                    />
                  </span>
                </span>
                <span className="text-[12.5px] leading-5 text-muted-foreground">
                  {t(BASE_DESCRIPTIONS[base])}
                </span>
              </label>
            );
          })}
        </div>
        {value.base === "custom" ? (
          <div className="onboarding-field-enter mt-3">
            <label
              htmlFor={`${idPrefix}-custom`}
              className="mb-2 block text-[12.5px] text-muted-foreground"
            >
              {t("profile.style.customLabel")}
            </label>
            <Textarea
              id={`${idPrefix}-custom`}
              value={value.custom}
              onChange={(event) => update({ custom: event.target.value })}
              placeholder={t("profile.style.customPlaceholder")}
              rows={4}
              maxLength={1000}
              className="rounded-2xl border-white/60 bg-white/50 text-[14px] leading-6 backdrop-blur-md dark:border-white/10 dark:bg-white/[0.05]"
            />
          </div>
        ) : null}
      </fieldset>

      <div>
        <p className="mb-1 text-[13px] font-medium text-foreground/80">
          {t("profile.style.fineTuneLabel")}
        </p>
        <p className="mb-4 text-[12.5px] leading-5 text-muted-foreground">
          {t("profile.style.fineTuneHint")}
        </p>
        <div className="space-y-3">
          {STYLE_DIMENSIONS.map((dimension) => (
            <div
              key={dimension}
              className="flex flex-col gap-3 rounded-2xl border border-white/50 bg-white/25 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between dark:border-white/10 dark:bg-white/[0.03]"
            >
              <div className="min-w-0">
                <p className="text-[13.5px] font-medium text-foreground/90">
                  {t(`profile.style.dimension.${dimension}`)}
                </p>
                <p className="mt-0.5 text-[12px] leading-[1.15rem] text-muted-foreground">
                  {t(DIMENSION_DESCRIPTIONS[dimension])}
                </p>
              </div>
              <SegmentedControl<StyleTuning>
                mode="radio"
                ariaLabel={t(`profile.style.dimension.${dimension}`)}
                value={value[dimension]}
                options={STYLE_TUNINGS.map((tuning) => ({
                  value: tuning,
                  label: t(`profile.style.tuning.${tuning}`),
                }))}
                onChange={(tuning) => update({ [dimension]: tuning } as Partial<ChatStyle>)}
                className="shrink-0"
                itemClassName="min-w-[3.6rem] px-3 py-1.5 text-[12.5px]"
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
