import { useCallback, useEffect, useSyncExternalStore } from "react";

import { fetchHomeWidgets, saveHomeWidgets } from "@/lib/api";
import type { HomeWidgetEntry, HomeWidgetsPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

import { HOME_WIDGETS } from "./registry";

const CACHE_KEY = "yoreni-webui.home-widgets";

function defaultEntry(visible: boolean): HomeWidgetEntry {
  return { visible, collapsed: false };
}

/** Fill registry defaults so a partially stored layout still renders completely. */
function completeLayout(value: unknown): HomeWidgetsPayload {
  const stored = (value && typeof value === "object" ? value : {}) as Partial<HomeWidgetsPayload>;
  const widgets: Record<string, HomeWidgetEntry> = {};
  for (const definition of HOME_WIDGETS) {
    const entry = stored.widgets?.[definition.id];
    widgets[definition.id] = {
      visible: typeof entry?.visible === "boolean" ? entry.visible : definition.defaultVisible,
      collapsed: entry?.collapsed === true,
    };
  }
  const order: string[] = [];
  for (const id of Array.isArray(stored.order) ? stored.order : []) {
    if (id in widgets && !order.includes(id)) order.push(id);
  }
  for (const definition of HOME_WIDGETS) {
    if (!order.includes(definition.id)) order.push(definition.id);
  }
  return { order, widgets };
}

function readCache(): HomeWidgetsPayload {
  try {
    const raw = window.localStorage.getItem(CACHE_KEY);
    return completeLayout(raw ? JSON.parse(raw) : null);
  } catch {
    return completeLayout(null);
  }
}

let snapshot = readCache();
const listeners = new Set<() => void>();

function publish(layout: HomeWidgetsPayload) {
  snapshot = layout;
  try {
    window.localStorage.setItem(CACHE_KEY, JSON.stringify(layout));
  } catch {
    // The cache only shortens the first paint; the gateway owns the layout.
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot() {
  return snapshot;
}

/** Drop the cached layout so the next render starts from registry defaults. */
export function resetHomeWidgets() {
  snapshot = completeLayout(null);
}

function withEntry(layout: HomeWidgetsPayload, id: string, patch: Partial<HomeWidgetEntry>) {
  const entry = layout.widgets[id] ?? defaultEntry(true);
  return {
    ...layout,
    widgets: { ...layout.widgets, [id]: { ...entry, ...patch } },
  };
}

function moveWidget(layout: HomeWidgetsPayload, id: string, delta: -1 | 1) {
  const order = [...layout.order];
  const index = order.indexOf(id);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= order.length) return layout;
  order[index] = order[target];
  order[target] = id;
  return { ...layout, order };
}

export interface HomeWidgetState extends HomeWidgetEntry {
  id: string;
  collapsible: boolean;
}

/** Home widget layout: cached locally for the first paint, owned by the gateway. */
export function useHomeWidgets() {
  const { client, getToken } = useClient();
  const layout = useSyncExternalStore(subscribe, getSnapshot);

  useEffect(() => {
    let cancelled = false;
    const refresh = () => {
      void fetchHomeWidgets(getToken())
        .then((payload) => {
          if (!cancelled) publish(completeLayout(payload));
        })
        .catch(() => {});
    };
    refresh();
    window.addEventListener("focus", refresh);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", refresh);
    };
  }, [getToken]);

  useEffect(() => {
    const refresh = () => publish(readCache());
    window.addEventListener("storage", refresh);
    return () => window.removeEventListener("storage", refresh);
  }, []);

  const save = useCallback((next: HomeWidgetsPayload) => {
    publish(next);
    void saveHomeWidgets(client, next)
      .then((saved) => publish(completeLayout(saved)))
      .catch(() => {});
  }, [client]);

  const setVisible = useCallback((id: string, visible: boolean) => {
    save(withEntry(getSnapshot(), id, { visible }));
  }, [save]);

  const toggleCollapsed = useCallback((id: string) => {
    const current = getSnapshot();
    save(withEntry(current, id, { collapsed: !(current.widgets[id]?.collapsed ?? false) }));
  }, [save]);

  const move = useCallback((id: string, delta: -1 | 1) => {
    save(moveWidget(getSnapshot(), id, delta));
  }, [save]);

  const states: HomeWidgetState[] = layout.order.map((id) => {
    const definition = HOME_WIDGETS.find((widget) => widget.id === id);
    const entry = layout.widgets[id] ?? defaultEntry(true);
    return {
      id,
      visible: entry.visible,
      collapsed: entry.collapsed,
      collapsible: definition?.collapsible ?? false,
    };
  });

  return { order: layout.order, states, setVisible, toggleCollapsed, move };
}
