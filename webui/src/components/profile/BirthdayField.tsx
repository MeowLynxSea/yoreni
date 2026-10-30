import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const CURRENT_YEAR = new Date().getFullYear();

interface Draft {
  year: number | null;
  month: number | null;
  day: number | null;
}

function daysInMonth(year: number, month: number): number {
  return new Date(year, month, 0).getDate();
}

function parse(value: string | null): Draft {
  if (!value) return { year: null, month: null, day: null };
  const [year, month, day] = value.split("-").map((part) => Number(part));
  return {
    year: Number.isFinite(year) ? year : null,
    month: Number.isFinite(month) ? month : null,
    day: Number.isFinite(day) ? day : null,
  };
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function isoFrom(draft: Draft): string | null {
  const { year, month, day } = draft;
  return year && month && day ? `${year}-${pad(month)}-${pad(day)}` : null;
}

export function BirthdayField({
  value,
  onChange,
  idPrefix = "birthday",
}: {
  value: string | null;
  onChange: (next: string | null) => void;
  idPrefix?: string;
}) {
  const { t, i18n } = useTranslation();
  // A partial pick is a draft: the stored value only exists once all three
  // parts are chosen, so the selects must keep their own selection meanwhile.
  const [draft, setDraft] = useState<Draft>(() => parse(value));
  const lastEmitted = useRef<string | null>(value);

  useEffect(() => {
    if (value === lastEmitted.current) return;
    lastEmitted.current = value;
    setDraft(parse(value));
  }, [value]);

  const years = useMemo(
    () => Array.from({ length: CURRENT_YEAR - 1919 }, (_, index) => CURRENT_YEAR - index),
    [],
  );
  const months = useMemo(
    () => Array.from({ length: 12 }, (_, index) => index + 1),
    [],
  );
  const maxDay = draft.year && draft.month ? daysInMonth(draft.year, draft.month) : 31;
  const days = useMemo(() => Array.from({ length: maxDay }, (_, index) => index + 1), [maxDay]);

  const update = (next: Partial<Draft>) => {
    const merged: Draft = { ...draft, ...next };
    if (merged.year && merged.month && merged.day) {
      const limit = daysInMonth(merged.year, merged.month);
      if (merged.day > limit) merged.day = limit;
    }
    setDraft(merged);
    const iso = isoFrom(merged);
    lastEmitted.current = iso;
    onChange(iso);
  };

  const monthLabel = new Intl.DateTimeFormat(i18n.resolvedLanguage || undefined, { month: "long" });

  return (
    <div id={`${idPrefix}-field`} className="flex flex-wrap items-center gap-2.5">
      <label className="sr-only" htmlFor={`${idPrefix}-year`}>
        {t("onboarding.birthday.year")}
      </label>
      <div className="onboarding-date-select">
        <select
          id={`${idPrefix}-year`}
          value={draft.year ?? ""}
          onChange={(event) =>
            update({ year: event.target.value ? Number(event.target.value) : null })
          }
        >
          <option value="">{t("onboarding.birthday.yearPlaceholder")}</option>
          {years.map((year) => (
            <option key={year} value={year}>
              {year}
            </option>
          ))}
        </select>
      </div>
      <label className="sr-only" htmlFor={`${idPrefix}-month`}>
        {t("onboarding.birthday.month")}
      </label>
      <div className="onboarding-date-select">
        <select
          id={`${idPrefix}-month`}
          value={draft.month ?? ""}
          onChange={(event) =>
            update({ month: event.target.value ? Number(event.target.value) : null })
          }
        >
          <option value="">{t("onboarding.birthday.monthPlaceholder")}</option>
          {months.map((month) => (
            <option key={month} value={month}>
              {monthLabel.format(new Date(2024, month - 1, 1))}
            </option>
          ))}
        </select>
      </div>
      <label className="sr-only" htmlFor={`${idPrefix}-day`}>
        {t("onboarding.birthday.day")}
      </label>
      <div className="onboarding-date-select">
        <select
          id={`${idPrefix}-day`}
          value={draft.day ?? ""}
          onChange={(event) =>
            update({ day: event.target.value ? Number(event.target.value) : null })
          }
        >
          <option value="">{t("onboarding.birthday.dayPlaceholder")}</option>
          {days.map((day) => (
            <option key={day} value={day}>
              {day}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}
