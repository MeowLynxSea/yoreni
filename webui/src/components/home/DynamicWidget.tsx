import { CircleAlert, ExternalLink } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useThemeValue } from "@/hooks/useTheme";
import { fetchWidget, refreshWidget } from "@/lib/api";
import type { DynamicWidgetSnapshot, DynamicWidgetSummary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useClient } from "@/providers/ClientProvider";

import {
  DEFAULT_WIDGET_HEIGHT,
  WIDGET_DATA_MESSAGE,
  WIDGET_FRAME_SANDBOX,
  WIDGET_HEIGHT_MESSAGE,
  WIDGET_REFRESH_MESSAGE,
  clampWidgetHeight,
  readWidgetTheme,
  widgetDocument,
} from "./dynamic-widget";
import { WidgetCard } from "./WidgetCard";

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function StatView({ data }: { data: Record<string, unknown> }) {
  const value = data.value;
  if (typeof value !== "string" && typeof value !== "number") return null;
  const label = typeof data.label === "string" ? data.label : "";
  const detail = typeof data.detail === "string" ? data.detail : "";
  return (
    <div>
      <p className="display-serif text-[1.9rem] leading-none text-foreground">{String(value)}</p>
      {label ? <p className="mt-1.5 text-[13px] leading-5 text-foreground/80">{label}</p> : null}
      {detail ? <p className="mt-0.5 text-[12px] leading-5 text-muted-foreground">{detail}</p> : null}
    </div>
  );
}

interface ListRow {
  title: string;
  detail?: string;
  meta?: string;
}

function listRow(value: unknown): ListRow | null {
  const row = asRecord(value);
  if (!row || typeof row.title !== "string") return null;
  return {
    title: row.title,
    detail: typeof row.detail === "string" ? row.detail : undefined,
    meta: typeof row.meta === "string" ? row.meta : undefined,
  };
}

function ListView({ data }: { data: Record<string, unknown> }) {
  if (!Array.isArray(data.items)) return null;
  const items = data.items.map(listRow).filter((row): row is ListRow => row !== null);
  if (!items.length) return null;
  return (
    <ul className="flex flex-col gap-2.5">
      {items.map((item, index) => (
        <li key={index} className="flex items-baseline justify-between gap-3">
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] leading-5 text-foreground/90">{item.title}</span>
            {item.detail ? (
              <span className="block truncate text-[12px] leading-5 text-muted-foreground">{item.detail}</span>
            ) : null}
          </span>
          {item.meta ? (
            <span className="shrink-0 text-[12px] tabular-nums text-muted-foreground">{item.meta}</span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function TableView({ data }: { data: Record<string, unknown> }) {
  const columns = Array.isArray(data.columns)
    ? data.columns.filter((column): column is string => typeof column === "string")
    : [];
  const rows = Array.isArray(data.rows) ? data.rows : [];
  if (!columns.length || !rows.length) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-[12px] leading-5">
        <thead>
          <tr>
            {columns.map((column, index) => (
              <th
                key={index}
                scope="col"
                className="whitespace-nowrap pb-1.5 pr-3 font-medium text-muted-foreground last:pr-0"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-t border-border/50">
              {(Array.isArray(row) ? row : []).map((cell, cellIndex) => (
                <td key={cellIndex} className="py-1.5 pr-3 align-top text-foreground/90 last:pr-0">
                  {typeof cell === "string" || typeof cell === "number" ? String(cell) : ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TextView({ data }: { data: Record<string, unknown> }) {
  if (typeof data.text !== "string" || !data.text) return null;
  return <p className="whitespace-pre-wrap text-[13px] leading-6 text-foreground/90">{data.text}</p>;
}

const TONE_TEXT = {
  positive: "text-emerald-700 dark:text-emerald-300",
  warning: "text-amber-700 dark:text-amber-300",
  critical: "text-red-700 dark:text-red-300",
} as const;

const TONE_FILL = {
  positive: "bg-emerald-500/80",
  warning: "bg-amber-500/80",
  critical: "bg-red-500/80",
} as const;

function toneClass(value: unknown, tones: Record<keyof typeof TONE_TEXT, string>): string | undefined {
  return value === "positive" || value === "warning" || value === "critical"
    ? tones[value]
    : undefined;
}

interface Metric {
  label: string;
  value: string;
  detail?: string;
  tone: unknown;
}

function metricItem(value: unknown): Metric | null {
  const row = asRecord(value);
  if (!row || typeof row.label !== "string") return null;
  if (typeof row.value !== "string" && typeof row.value !== "number") return null;
  return {
    label: row.label,
    value: String(row.value),
    detail: typeof row.detail === "string" ? row.detail : undefined,
    tone: row.tone,
  };
}

function MetricsView({ data }: { data: Record<string, unknown> }) {
  if (!Array.isArray(data.items)) return null;
  const items = data.items.map(metricItem).filter((item): item is Metric => item !== null);
  if (!items.length) return null;
  const columns = data.columns === 1 || data.columns === 3 ? data.columns : 2;
  return (
    <div
      className={cn(
        "grid gap-2.5",
        columns === 1 ? "grid-cols-1" : columns === 3 ? "grid-cols-3" : "grid-cols-2",
      )}
    >
      {items.map((item, index) => (
        <div key={index} className="min-w-0 rounded-2xl bg-foreground/[0.05] px-3.5 py-3">
          <p className="truncate text-[12px] leading-5 text-muted-foreground">{item.label}</p>
          <p
            className={cn(
              "mt-0.5 truncate text-[1.35rem] leading-tight tabular-nums text-foreground",
              toneClass(item.tone, TONE_TEXT),
            )}
          >
            {item.value}
          </p>
          {item.detail ? (
            <p className="mt-0.5 truncate text-[12px] leading-5 text-muted-foreground">{item.detail}</p>
          ) : null}
        </div>
      ))}
    </div>
  );
}

interface ProgressRow {
  label: string;
  value: number;
  max: number;
  detail?: string;
  tone: unknown;
}

function progressItem(value: unknown): ProgressRow | null {
  const row = asRecord(value);
  if (!row || typeof row.label !== "string") return null;
  if (typeof row.value !== "number" || !Number.isFinite(row.value)) return null;
  const max = typeof row.max === "number" && Number.isFinite(row.max) && row.max > 0 ? row.max : 100;
  return {
    label: row.label,
    value: Math.max(0, row.value),
    max,
    detail: typeof row.detail === "string" ? row.detail : undefined,
    tone: row.tone,
  };
}

function ProgressView({ data }: { data: Record<string, unknown> }) {
  if (!Array.isArray(data.items)) return null;
  const rows = data.items.map(progressItem).filter((row): row is ProgressRow => row !== null);
  if (!rows.length) return null;
  return (
    <ul className="flex flex-col gap-3">
      {rows.map((row, index) => {
        const percent = Math.round(Math.min(1, row.value / row.max) * 100);
        return (
          <li key={index}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="min-w-0 truncate text-[13px] leading-5 text-foreground/90">
                {row.label}
              </span>
              <span className="shrink-0 text-[12px] tabular-nums text-muted-foreground">
                {row.detail ?? `${percent}%`}
              </span>
            </div>
            <div
              role="progressbar"
              aria-label={row.label}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
              className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-foreground/10"
            >
              <div
                className={cn("h-full rounded-full", toneClass(row.tone, TONE_FILL) ?? "bg-primary/70")}
                style={{ width: `${percent}%` }}
              />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function FactsView({ data }: { data: Record<string, unknown> }) {
  if (!Array.isArray(data.items)) return null;
  const items = data.items.map(metricItem).filter((item): item is Metric => item !== null);
  if (!items.length) return null;
  return (
    <dl className="flex flex-col">
      {items.map((item, index) => (
        <div
          key={index}
          className={cn(
            "flex items-baseline justify-between gap-3 py-1.5",
            index > 0 && "border-t border-border/50",
          )}
        >
          <dt className="min-w-0 truncate text-[12px] leading-5 text-muted-foreground">{item.label}</dt>
          <dd
            className={cn(
              "min-w-0 truncate text-[13px] leading-5 tabular-nums text-foreground/90",
              toneClass(item.tone, TONE_TEXT),
            )}
          >
            {item.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}

interface ChartPoint {
  label: string;
  value: number;
}

function chartPoints(value: unknown): ChartPoint[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((entry) => {
      const row = asRecord(entry);
      if (!row || typeof row.label !== "string") return null;
      if (typeof row.value !== "number" || !Number.isFinite(row.value) || row.value < 0) return null;
      return { label: row.label, value: row.value };
    })
    .filter((point): point is ChartPoint => point !== null);
}

function ChartView({ data }: { data: Record<string, unknown> }) {
  const points = chartPoints(data.items);
  if (!points.length) return null;
  const limit = typeof data.max === "number" && data.max > 0 ? data.max : 0;
  const peak = Math.max(limit, ...points.map((point) => point.value), 1);
  const summary = points.map((point) => `${point.label} ${point.value}`).join(", ");
  const first = points[0];
  const last = points[points.length - 1];
  const labels = points.length <= 7 ? (
    <div className="flex gap-1.5 text-[10px] leading-4 text-muted-foreground">
      {points.map((point, index) => (
        <span key={index} className="min-w-0 flex-1 truncate text-center">
          {point.label}
        </span>
      ))}
    </div>
  ) : (
    <div className="flex justify-between gap-3 text-[10px] leading-4 text-muted-foreground">
      <span className="truncate">{first.label}</span>
      <span className="truncate">{last.label}</span>
    </div>
  );

  if (data.type === "line" && points.length > 1) {
    const step = 100 / (points.length - 1);
    const coordinates = points.map((point, index) => {
      const x = index * step;
      const y = 100 - (point.value / peak) * 100;
      return `${x},${y}`;
    });
    return (
      <figure className="flex flex-col gap-1.5">
        <svg
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          role="img"
          aria-label={summary}
          className="h-24 w-full"
        >
          <polygon points={`0,100 ${coordinates.join(" ")} 100,100`} className="fill-primary/10" />
          <polyline
            points={coordinates.join(" ")}
            className="fill-none stroke-primary"
            strokeWidth={1.5}
            vectorEffect="non-scaling-stroke"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        {labels}
      </figure>
    );
  }

  return (
    <figure className="flex flex-col gap-1.5">
      <div role="img" aria-label={summary} className="flex h-24 items-end gap-1.5">
        {points.map((point, index) => (
          <div
            key={index}
            title={`${point.label} ${point.value}`}
            className="flex h-full min-w-0 flex-1 items-end"
          >
            <div
              className="w-full rounded-t-[4px] bg-primary/55"
              style={{ height: `${point.value === 0 ? 0 : Math.max(2, (point.value / peak) * 100)}%` }}
            />
          </div>
        ))}
      </div>
      {labels}
    </figure>
  );
}

interface TimelineRow {
  title: string;
  detail?: string;
  meta?: string;
  tone: unknown;
}

function timelineItem(value: unknown): TimelineRow | null {
  const row = asRecord(value);
  if (!row || typeof row.title !== "string") return null;
  return {
    title: row.title,
    detail: typeof row.detail === "string" ? row.detail : undefined,
    meta: typeof row.meta === "string" ? row.meta : undefined,
    tone: row.tone,
  };
}

const TONE_DOT = {
  positive: "bg-emerald-500",
  warning: "bg-amber-500",
  critical: "bg-red-500",
} as const;

function TimelineView({ data }: { data: Record<string, unknown> }) {
  if (!Array.isArray(data.items)) return null;
  const items = data.items.map(timelineItem).filter((item): item is TimelineRow => item !== null);
  if (!items.length) return null;
  return (
    <ol className="flex flex-col gap-3">
      {items.map((item, index) => (
        <li key={index} className="flex items-stretch gap-3">
          <span aria-hidden className="flex flex-col items-center pt-1.5">
            <span className={cn("h-2 w-2 shrink-0 rounded-full", toneClass(item.tone, TONE_DOT) ?? "bg-foreground/25")} />
            {index < items.length - 1 ? <span className="mt-1 w-px flex-1 bg-border/70" /> : null}
          </span>
          <span className="min-w-0 flex-1 pb-1">
            <span className="block truncate text-[13px] leading-5 text-foreground/90">{item.title}</span>
            {item.detail ? (
              <span className="block text-[12px] leading-5 text-muted-foreground">{item.detail}</span>
            ) : null}
          </span>
          {item.meta ? (
            <span className="shrink-0 text-[12px] leading-5 tabular-nums text-muted-foreground">
              {item.meta}
            </span>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

const TONE_BADGE = {
  positive: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  warning: "bg-amber-500/14 text-amber-700 dark:text-amber-300",
  critical: "bg-red-500/12 text-red-700 dark:text-red-300",
} as const;

interface Badge {
  label: string;
  detail?: string;
  tone: unknown;
}

function badgeItem(value: unknown): Badge | null {
  const row = asRecord(value);
  if (!row || typeof row.label !== "string") return null;
  return {
    label: row.label,
    detail: typeof row.detail === "string" ? row.detail : undefined,
    tone: row.tone,
  };
}

function BadgesView({ data }: { data: Record<string, unknown> }) {
  if (!Array.isArray(data.items)) return null;
  const items = data.items.map(badgeItem).filter((item): item is Badge => item !== null);
  if (!items.length) return null;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {items.map((item, index) => (
        <li
          key={index}
          className={cn(
            "rounded-full px-2.5 py-1 text-[11px] leading-4",
            toneClass(item.tone, TONE_BADGE) ?? "bg-foreground/[0.06] text-muted-foreground",
          )}
        >
          {item.label}
          {item.detail ? <span className="ml-1 opacity-70">{item.detail}</span> : null}
        </li>
      ))}
    </ul>
  );
}

interface LinkRow {
  label: string;
  href: string;
  detail?: string;
  meta?: string;
}

function linkItem(value: unknown): LinkRow | null {
  const row = asRecord(value);
  if (!row || typeof row.label !== "string" || typeof row.href !== "string") return null;
  if (!row.href.startsWith("https://") && !row.href.startsWith("http://")) return null;
  return {
    label: row.label,
    href: row.href,
    detail: typeof row.detail === "string" ? row.detail : undefined,
    meta: typeof row.meta === "string" ? row.meta : undefined,
  };
}

function LinksView({ data }: { data: Record<string, unknown> }) {
  if (!Array.isArray(data.items)) return null;
  const items = data.items.map(linkItem).filter((item): item is LinkRow => item !== null);
  if (!items.length) return null;
  return (
    <ul className="flex flex-col">
      {items.map((item, index) => (
        <li key={index}>
          <a
            href={item.href}
            target="_blank"
            rel="noreferrer noopener"
            className="-mx-2 flex items-center justify-between gap-3 rounded-xl px-2 py-1.5 hover:bg-foreground/[0.04] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
          >
            <span className="min-w-0">
              <span className="block truncate text-[13px] leading-5 text-foreground/90">{item.label}</span>
              {item.detail ? (
                <span className="block truncate text-[12px] leading-5 text-muted-foreground">
                  {item.detail}
                </span>
              ) : null}
            </span>
            <span className="flex shrink-0 items-center gap-2 text-[12px] leading-5 text-muted-foreground">
              {item.meta ? <span className="tabular-nums">{item.meta}</span> : null}
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            </span>
          </a>
        </li>
      ))}
    </ul>
  );
}

function PanelBlock({ block }: { block: Record<string, unknown> }) {
  if (block.kind === "divider") return <hr className="border-0 border-t border-border/60" />;
  let content: ReactNode = null;
  if (block.kind === "text") content = <TextView data={block} />;
  else if (block.kind === "list") content = <ListView data={block} />;
  else if (block.kind === "table") content = <TableView data={block} />;
  else if (block.kind === "metrics") content = <MetricsView data={block} />;
  else if (block.kind === "progress") content = <ProgressView data={block} />;
  else if (block.kind === "facts") content = <FactsView data={block} />;
  else if (block.kind === "chart") content = <ChartView data={block} />;
  else if (block.kind === "timeline") content = <TimelineView data={block} />;
  else if (block.kind === "badges") content = <BadgesView data={block} />;
  else if (block.kind === "links") content = <LinksView data={block} />;
  if (!content) return null;
  const title = typeof block.title === "string" ? block.title : "";
  if (!title) return content;
  return (
    <section>
      <h3 className="mb-2 text-[12px] font-medium leading-5 text-muted-foreground">{title}</h3>
      {content}
    </section>
  );
}

function PanelView({ data }: { data: Record<string, unknown> }) {
  const lead = typeof data.lead === "string" ? data.lead : "";
  const note = typeof data.note === "string" ? data.note : "";
  const blocks = Array.isArray(data.blocks)
    ? data.blocks.map(asRecord).filter((block): block is Record<string, unknown> => block !== null)
    : [];
  if (!lead && !blocks.length && !note) return null;
  return (
    <div className="flex flex-col gap-4">
      {lead ? <p className="text-[13px] leading-5 text-foreground/80">{lead}</p> : null}
      {blocks.map((block, index) => (
        <PanelBlock key={index} block={block} />
      ))}
      {note ? <p className="text-[12px] leading-5 text-muted-foreground">{note}</p> : null}
    </div>
  );
}

function TemplateView({ view, data }: { view: DynamicWidgetSummary["view"]; data: unknown }) {
  const record = asRecord(data);
  if (!record) return null;
  if (view === "stat") return <StatView data={record} />;
  if (view === "list") return <ListView data={record} />;
  if (view === "table") return <TableView data={record} />;
  if (view === "text") return <TextView data={record} />;
  if (view === "metrics") return <MetricsView data={record} />;
  if (view === "progress") return <ProgressView data={record} />;
  if (view === "facts") return <FactsView data={record} />;
  if (view === "chart") return <ChartView data={record} />;
  if (view === "timeline") return <TimelineView data={record} />;
  if (view === "badges") return <BadgesView data={record} />;
  if (view === "links") return <LinksView data={record} />;
  if (view === "panel") return <PanelView data={record} />;
  return null;
}

/** A gateway-defined widget: template data rendered here, `view="html"` in a sandboxed frame. */
export function DynamicWidget({ id, summary }: { id: string; summary: DynamicWidgetSummary }) {
  const { t } = useTranslation();
  const { client, getToken } = useClient();
  const scheme = useThemeValue();
  const host = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLIFrameElement | null>(null);
  const frameReady = useRef(false);
  const busy = useRef(false);
  const live = useRef<{ html: string; theme: string; updatedAt: number | null } | null>(null);
  const [snapshot, setSnapshot] = useState<DynamicWidgetSnapshot | null>(null);
  const [source, setSource] = useState("");
  const [height, setHeight] = useState(DEFAULT_WIDGET_HEIGHT);

  const load = useCallback(async () => {
    try {
      setSnapshot(await fetchWidget(getToken(), id));
    } catch {
      // Keep the last snapshot; a transient fetch failure must not blank the panel.
    }
  }, [getToken, id]);

  useEffect(() => {
    void load();
    window.addEventListener("focus", load);
    const unsubscribe = client.onEvent((event) => {
      if (event.event === "widget_changed" && event.widget_id === id) void load();
    });
    const unsubscribeReady = client.onReady(load);
    return () => {
      window.removeEventListener("focus", load);
      unsubscribe();
      unsubscribeReady();
    };
  }, [client, id, load]);

  // Compose the document once, then push data-only updates into the live frame.
  // The theme class is applied in a layout effect, so the palette is current here.
  useEffect(() => {
    if (!snapshot || snapshot.widget.view !== "html") return;
    const html = snapshot.html ?? "";
    const theme = readWidgetTheme(host.current, scheme);
    const themeKey = JSON.stringify(theme);
    const updatedAt = snapshot.widget.updated_at_ms;
    const current = live.current;
    if (current && current.html === html && current.theme === themeKey && frameReady.current) {
      if (current.updatedAt === updatedAt) return;
      current.updatedAt = updatedAt;
      frame.current?.contentWindow?.postMessage(
        { type: WIDGET_DATA_MESSAGE, data: snapshot.data, updatedAt },
        "*",
      );
      return;
    }
    live.current = { html, theme: themeKey, updatedAt };
    frameReady.current = false;
    setSource(widgetDocument({ html, data: snapshot.data, updatedAt, theme }));
  }, [scheme, snapshot]);

  const refresh = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      await refreshWidget(client, id);
    } catch {
      // The reload surfaces the failure in the widget's error row.
    } finally {
      busy.current = false;
      await load();
    }
  }, [client, id, load]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const target = frame.current?.contentWindow;
      if (!target || event.source !== target) return;
      const message = event.data as { type?: unknown; height?: unknown } | null;
      if (!message || typeof message !== "object") return;
      if (message.type === WIDGET_HEIGHT_MESSAGE) setHeight(clampWidgetHeight(message.height));
      else if (message.type === WIDGET_REFRESH_MESSAGE) void refresh();
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [refresh]);

  const widget = snapshot?.widget;
  const view = widget?.view ?? summary.view;
  const data = snapshot?.data;
  const error = widget?.error ?? summary.error;

  const attachFrame = useCallback((node: HTMLIFrameElement | null) => {
    frame.current = node;
    if (!node) frameReady.current = false;
  }, []);

  // Never mount an empty frame: an unloaded iframe paints an opaque white box in dark mode.
  let body: ReactNode = null;
  if (view === "html" && widget) {
    body = source
      ? (
        <iframe
          ref={attachFrame}
          title={widget.title || summary.title}
          sandbox={WIDGET_FRAME_SANDBOX}
          referrerPolicy="no-referrer"
          srcDoc={source}
          onLoad={() => { frameReady.current = true; }}
          style={{ height, backgroundColor: "transparent" }}
          className="block w-full border-0 bg-transparent"
        />
      )
      : <div aria-hidden style={{ height }} />;
  } else if (view !== "html" && data !== null && data !== undefined) {
    body = <TemplateView view={view} data={data} />;
  }

  if (!body && !error) return null;

  return (
    <WidgetCard title={widget?.title || summary.title}>
      <div ref={host} className="text-[13px]">
        {body}
        {error ? (
          <p
            className="mt-2 flex items-center gap-1.5 text-[12px] leading-5 text-muted-foreground"
            title={error}
          >
            <CircleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />
            <span className="shrink-0">{t("home.widgets.dynamic.failed")}</span>
            <span className="min-w-0 truncate opacity-80">{error}</span>
          </p>
        ) : null}
      </div>
    </WidgetCard>
  );
}
