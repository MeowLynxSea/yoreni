import { useCallback, useEffect, useSyncExternalStore } from "react";

import { fetchHomeWidgets, fetchWidgets, saveHomeWidgets } from "@/lib/api";
import type { DynamicWidgetSummary, HomeWidgetEntry, HomeWidgetsPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

import { HOME_WIDGETS } from "./registry";

const CACHE_KEY = "yoreni-webui.home-widgets";
export const DYNAMIC_WIDGETS_CAPABILITY = "webui.widgets.v1";
const WIDGET_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

interface HomeWidgetStore {
  layout: HomeWidgetsPayload;
  dynamic: Record<string, DynamicWidgetSummary>;
  /** Whether dynamic definitions loaded; until then unknown ids stay in the layout. */
  dynamicLoaded: boolean;
}

function defaultEntry(visible: boolean): HomeWidgetEntry {
  return { visible, collapsed: false };
}

/**
 * Fill registry defaults so a partially stored layout still renders completely.
 * Unknown ids survive until a definitions load proves the widget is gone, so a
 * client that cannot list dynamic widgets never drops their stored placement.
 */
function completeLayout(
  value: unknown,
  dynamic: Record<string, DynamicWidgetSummary>,
  dynamicLoaded: boolean,
): HomeWidgetsPayload {
  const stored = (value && typeof value === "object" ? value : {}) as Partial<HomeWidgetsPayload>;
  const storedWidgets = (
    stored.widgets && typeof stored.widgets === "object" ? stored.widgets : {}
  ) as Record<string, HomeWidgetEntry | undefined>;
  const entryFor = (id: string, fallbackVisible: boolean): HomeWidgetEntry => {
    const entry = storedWidgets[id];
    return {
      visible: typeof entry?.visible === "boolean" ? entry.visible : fallbackVisible,
      collapsed: entry?.collapsed === true,
    };
  };

  const widgets: Record<string, HomeWidgetEntry> = {};
  for (const definition of HOME_WIDGETS) {
    widgets[definition.id] = entryFor(definition.id, definition.defaultVisible);
  }
  if (dynamicLoaded) {
    for (const id of Object.keys(dynamic)) {
      if (!(id in widgets)) widgets[id] = entryFor(id, true);
    }
  } else {
    for (const id of Object.keys(storedWidgets)) {
      if (id in widgets || !WIDGET_ID_RE.test(id)) continue;
      widgets[id] = entryFor(id, true);
    }
  }

  const order: string[] = [];
  for (const id of Array.isArray(stored.order) ? stored.order : []) {
    if (typeof id === "string" && id in widgets && !order.includes(id)) order.push(id);
  }
  for (const id of Object.keys(widgets)) {
    if (!order.includes(id)) order.push(id);
  }
  return { order, widgets };
}

function readStoredLayout(): unknown {
  try {
    const raw = window.localStorage.getItem(CACHE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function readCache(): HomeWidgetsPayload {
  return completeLayout(readStoredLayout(), {}, false);
}

let snapshot: HomeWidgetStore = {
  layout: readCache(),
  dynamic: {},
  dynamicLoaded: false,
};
const listeners = new Set<() => void>();

function publish(next: HomeWidgetStore) {
  snapshot = next;
  try {
    window.localStorage.setItem(CACHE_KEY, JSON.stringify(next.layout));
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
  snapshot = {
    layout: completeLayout(null, {}, false),
    dynamic: {},
    dynamicLoaded: false,
  };
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
  dynamic: DynamicWidgetSummary | null;
}

/** Home widget layout: cached locally for the first paint, owned by the gateway. */
export function useHomeWidgets() {
  const { client, getToken, webuiCapabilities } = useClient();
  const store = useSyncExternalStore(subscribe, getSnapshot);
  const dynamicEnabled = webuiCapabilities.includes(DYNAMIC_WIDGETS_CAPABILITY);

  useEffect(() => {
    let cancelled = false;
    const refresh = () => {
      if (!dynamicEnabled) {
        void fetchHomeWidgets(getToken())
          .then((layout) => {
            if (cancelled) return;
            const current = getSnapshot();
            publish({
              layout: completeLayout(layout, current.dynamic, current.dynamicLoaded),
              dynamic: current.dynamic,
              dynamicLoaded: current.dynamicLoaded,
            });
          })
          .catch(() => {});
        return;
      }
      void Promise.all([fetchHomeWidgets(getToken()), fetchWidgets(getToken())])
        .then(([layout, definitions]) => {
          if (cancelled) return;
          const dynamic: Record<string, DynamicWidgetSummary> = {};
          for (const widget of definitions.widgets) dynamic[widget.id] = widget;
          publish({
            layout: completeLayout(layout, dynamic, true),
            dynamic,
            dynamicLoaded: true,
          });
        })
        .catch(() => {});
    };
    refresh();
    window.addEventListener("focus", refresh);
    const unsubscribe = client.onEvent((frame) => {
      if (frame.event !== "widget_changed" || frame.kind === "data") return;
      refresh();
    });
    // A reconnect may have missed change frames; reread the gateway's view of the widgets.
    const unsubscribeReady = client.onReady(refresh);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", refresh);
      unsubscribe();
      unsubscribeReady();
    };
  }, [client, dynamicEnabled, getToken]);

  useEffect(() => {
    const refresh = () => {
      const current = getSnapshot();
      publish({
        layout: completeLayout(readStoredLayout(), current.dynamic, current.dynamicLoaded),
        dynamic: current.dynamic,
        dynamicLoaded: current.dynamicLoaded,
      });
    };
    window.addEventListener("storage", refresh);
    return () => window.removeEventListener("storage", refresh);
  }, []);

  const save = useCallback((next: HomeWidgetsPayload) => {
    const current = getSnapshot();
    publish({ ...current, layout: next });
    void saveHomeWidgets(client, next)
      .then((saved) => {
        const latest = getSnapshot();
        publish({
          ...latest,
          layout: completeLayout(saved, latest.dynamic, latest.dynamicLoaded),
        });
      })
      .catch(() => {});
  }, [client]);

  const setVisible = useCallback((id: string, visible: boolean) => {
    save(withEntry(getSnapshot().layout, id, { visible }));
  }, [save]);

  const toggleCollapsed = useCallback((id: string) => {
    const current = getSnapshot().layout;
    save(withEntry(current, id, { collapsed: !(current.widgets[id]?.collapsed ?? false) }));
  }, [save]);

  const move = useCallback((id: string, delta: -1 | 1) => {
    save(moveWidget(getSnapshot().layout, id, delta));
  }, [save]);

  const states: HomeWidgetState[] = store.layout.order.map((id) => {
    const definition = HOME_WIDGETS.find((widget) => widget.id === id);
    const dynamic = store.dynamic[id] ?? null;
    const entry = store.layout.widgets[id] ?? defaultEntry(true);
    return {
      id,
      visible: entry.visible,
      collapsed: entry.collapsed,
      collapsible: definition?.collapsible ?? dynamic !== null,
      dynamic,
    };
  });

  return { order: store.layout.order, states, setVisible, toggleCollapsed, move };
}
