import i18n from "i18next";

import { normalizeLocale } from "@/i18n/config";

/** Loose ISO-or-epoch parser; zero denotes an unknown persisted message time. */
function parseDate(value: string | number | null | undefined): Date | null {
  if (value === null || value === undefined || value === "" || value === 0) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

const RELATIVE_THRESHOLDS: [number, Intl.RelativeTimeFormatUnit][] = [
  [60, "second"],
  [60, "minute"],
  [24, "hour"],
  [7, "day"],
  [4.345, "week"],
  [12, "month"],
  [Number.POSITIVE_INFINITY, "year"],
];

const relativeTimeFormatters = new Map<string, Intl.RelativeTimeFormat>();
const dateTimeFormatters = new Map<string, Intl.DateTimeFormat>();
const clockTimeFormatters = new Map<string, Intl.DateTimeFormat>();
const hourClockFormatters = new Map<string, Intl.DateTimeFormat>();
const weekdayFormatters = new Map<string, Intl.DateTimeFormat>();
const monthDayFormatters = new Map<string, Intl.DateTimeFormat>();
const fullDateFormatters = new Map<string, Intl.DateTimeFormat>();

/** A pause this long starts a new time group, the way chat apps break a transcript. */
export const MESSAGE_TIME_GROUP_GAP_MS = 10 * 60 * 1000;

function activeLocale(locale?: string): string {
  return locale || i18n.resolvedLanguage || i18n.language || normalizeLocale(i18n.resolvedLanguage ?? i18n.language);
}

function relativeTimeFormatter(locale: string): Intl.RelativeTimeFormat {
  const existing = relativeTimeFormatters.get(locale);
  if (existing) return existing;
  const formatter = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  relativeTimeFormatters.set(locale, formatter);
  return formatter;
}

function dateTimeFormatter(locale: string): Intl.DateTimeFormat {
  const existing = dateTimeFormatters.get(locale);
  if (existing) return existing;
  const formatter = new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  dateTimeFormatters.set(locale, formatter);
  return formatter;
}

function clockTimeFormatter(locale: string): Intl.DateTimeFormat {
  const existing = clockTimeFormatters.get(locale);
  if (existing) return existing;
  const formatter = new Intl.DateTimeFormat(locale, {
    hour: "2-digit",
    minute: "2-digit",
  });
  clockTimeFormatters.set(locale, formatter);
  return formatter;
}

/** Chat transcripts read on a 24-hour clock even where the locale prefers AM/PM. */
function hourClockTimeFormatter(locale: string): Intl.DateTimeFormat {
  return cachedFormatter(hourClockFormatters, locale, {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
}

function isSameLocalCalendarDay(left: Date, right: Date): boolean {
  return (
    left.getFullYear() === right.getFullYear()
    && left.getMonth() === right.getMonth()
    && left.getDate() === right.getDate()
  );
}

function cachedFormatter(
  cache: Map<string, Intl.DateTimeFormat>,
  locale: string,
  options: Intl.DateTimeFormatOptions,
): Intl.DateTimeFormat {
  const existing = cache.get(locale);
  if (existing) return existing;
  const formatter = new Intl.DateTimeFormat(locale, options);
  cache.set(locale, formatter);
  return formatter;
}

function weekdayFormatter(locale: string): Intl.DateTimeFormat {
  return cachedFormatter(weekdayFormatters, locale, { weekday: "short" });
}

function monthDayFormatter(locale: string): Intl.DateTimeFormat {
  return cachedFormatter(monthDayFormatters, locale, { month: "numeric", day: "numeric" });
}

function fullDateFormatter(locale: string): Intl.DateTimeFormat {
  return cachedFormatter(fullDateFormatters, locale, {
    year: "numeric",
    month: "numeric",
    day: "numeric",
  });
}

function daysBetweenCalendarDates(earlier: Date, later: Date): number {
  const start = Date.UTC(earlier.getFullYear(), earlier.getMonth(), earlier.getDate());
  const end = Date.UTC(later.getFullYear(), later.getMonth(), later.getDate());
  return Math.round((end - start) / 86_400_000);
}

export function relativeTime(
  value: string | number | null | undefined,
  locale?: string,
): string {
  const date = parseDate(value);
  if (!date) return "";
  let delta = (date.getTime() - Date.now()) / 1000;
  const formatter = relativeTimeFormatter(activeLocale(locale));
  for (const [step, unit] of RELATIVE_THRESHOLDS) {
    if (Math.abs(delta) < step) {
      return formatter.format(Math.round(delta), unit);
    }
    delta /= step;
  }
  return formatter.format(Math.round(delta), "year");
}

export function fmtDateTime(
  value: string | number | null | undefined,
  locale?: string,
): string {
  const date = parseDate(value);
  return date ? dateTimeFormatter(activeLocale(locale)).format(date) : "";
}

/**
 * Format a completion timestamp in the browser's local timezone.
 * Today's messages stay compact; older messages include their date for orientation.
 */
export function formatMessageEndTime(
  value: number | null | undefined,
  locale?: string,
): string {
  const date = parseDate(value);
  if (!date) return "";
  const loc = activeLocale(locale);
  return isSameLocalCalendarDay(date, new Date())
    ? clockTimeFormatter(loc).format(date)
    : dateTimeFormatter(loc).format(date);
}

/** A message's local date and clock time, split so the gutter can stack them. */
export interface MessageHoverTimeParts {
  /** Empty for messages sent today; the clock alone already identifies them. */
  date: string;
  time: string;
  /** Locale-formatted date and time for tooltips and assistive labels. */
  full: string;
}

export function formatMessageHoverTimeParts(
  value: number | null | undefined,
  locale?: string,
  now = new Date(),
): MessageHoverTimeParts | null {
  const date = parseDate(value);
  if (!date) return null;
  const loc = activeLocale(locale);
  const sameYear = date.getFullYear() === now.getFullYear();
  return {
    date: isSameLocalCalendarDay(date, now)
      ? ""
      : (sameYear ? monthDayFormatter(loc) : fullDateFormatter(loc)).format(date),
    time: hourClockTimeFormatter(loc).format(date),
    full: dateTimeFormatter(loc).format(date),
  };
}

/** True when this message starts a new time group rather than joining the previous one. */
export function startsMessageTimeGroup(
  previous: number | null | undefined,
  current: number | null | undefined,
): boolean {
  const value = parseDate(current);
  if (!value) return false;
  const earlier = parseDate(previous);
  if (!earlier) return true;
  if (!isSameLocalCalendarDay(earlier, value)) return true;
  if (value.getTime() < earlier.getTime()) return true;
  return value.getTime() - earlier.getTime() >= MESSAGE_TIME_GROUP_GAP_MS;
}

/**
 * Label for a time group's first message: the clock today, the day yesterday,
 * the weekday within a week, then a date. Chat apps orient readers this way.
 */
export function formatMessageGroupTime(
  value: number | null | undefined,
  locale?: string,
  now = new Date(),
): string {
  const date = parseDate(value);
  if (!date) return "";
  const loc = activeLocale(locale);
  const clock = clockTimeFormatter(loc).format(date);
  if (isSameLocalCalendarDay(date, now)) return clock;
  const elapsedDays = daysBetweenCalendarDates(date, now);
  if (elapsedDays === 1) return `${relativeTimeFormatter(loc).format(-1, "day")} ${clock}`;
  if (elapsedDays > 1 && elapsedDays < 7) return `${weekdayFormatter(loc).format(date)} ${clock}`;
  if (date.getFullYear() === now.getFullYear()) {
    return `${monthDayFormatter(loc).format(date)} ${clock}`;
  }
  return `${fullDateFormatter(loc).format(date)} ${clock}`;
}

/** Human-readable turn duration (wall-clock), locale-aware via ``Intl`` (seconds/minutes). */
export function formatTurnLatency(ms: number, locale?: string): string {
  const loc = activeLocale(locale);
  const msClamped = Math.max(0, ms);
  const secTotal = msClamped / 1000;
  if (secTotal < 60) {
    return new Intl.NumberFormat(loc, {
      style: "unit",
      unit: "second",
      unitDisplay: "narrow",
      maximumFractionDigits: secTotal < 10 ? 1 : 0,
      minimumFractionDigits: 0,
    }).format(secTotal);
  }
  const wholeMin = Math.floor(secTotal / 60);
  const remSec = Math.max(0, Math.round(secTotal - wholeMin * 60));
  const minStr = new Intl.NumberFormat(loc, {
    style: "unit",
    unit: "minute",
    unitDisplay: "narrow",
  }).format(wholeMin);
  const secStr = new Intl.NumberFormat(loc, {
    style: "unit",
    unit: "second",
    unitDisplay: "narrow",
    maximumFractionDigits: 0,
  }).format(remSec);
  return `${minStr}\u00a0${secStr}`;
}
