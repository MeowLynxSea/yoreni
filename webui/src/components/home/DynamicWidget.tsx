import { CircleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { useThemeValue } from "@/hooks/useTheme";
import { fetchWidget, refreshWidget } from "@/lib/api";
import type { DynamicWidgetSnapshot, DynamicWidgetSummary } from "@/lib/types";
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

function TemplateView({ view, data }: { view: DynamicWidgetSummary["view"]; data: unknown }) {
  const record = asRecord(data);
  if (!record) return null;
  if (view === "stat") return <StatView data={record} />;
  if (view === "list") return <ListView data={record} />;
  if (view === "table") return <TableView data={record} />;
  if (view === "text") return <TextView data={record} />;
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
