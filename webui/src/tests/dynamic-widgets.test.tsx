import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DynamicWidget } from "@/components/home/DynamicWidget";
import {
  MAX_WIDGET_HEIGHT,
  MIN_WIDGET_HEIGHT,
  WIDGET_FRAME_SANDBOX,
  clampWidgetHeight,
  widgetDocument,
  type WidgetTheme,
} from "@/components/home/dynamic-widget";
import { HomeFeed } from "@/components/home/HomePanel";
import { resetHomeWidgets } from "@/components/home/widget-layout";
import { WidgetsSettings } from "@/components/settings/overview/WidgetsSettings";
import {
  deleteWidget,
  fetchAutomations,
  fetchGoal,
  fetchHomeWidgets,
  fetchMemoryBackfill,
  fetchWidget,
  fetchWidgets,
  refreshWidget,
  saveHomeWidgets,
} from "@/lib/api";
import type { DynamicWidgetSnapshot, DynamicWidgetSummary, HomeWidgetsPayload } from "@/lib/types";
import type { AgentFrame, YoreniClient } from "@/lib/yoreni-client";
import { ClientProvider } from "@/providers/ClientProvider";

vi.mock("@/lib/api", () => ({
  deleteWidget: vi.fn(),
  fetchAutomations: vi.fn(),
  fetchGoal: vi.fn(),
  fetchHomeWidgets: vi.fn(),
  fetchMemoryBackfill: vi.fn(),
  fetchWidget: vi.fn(),
  fetchWidgets: vi.fn(),
  refreshWidget: vi.fn(),
  saveHomeWidgets: vi.fn(),
  setMemoryBackfillPaused: vi.fn(),
}));

const THEME: WidgetTheme = {
  scheme: "light",
  foreground: "#111111",
  mutedForeground: "#666666",
  border: "#dddddd",
  primary: "#000000",
  fontFamily: "sans-serif",
  fontSize: "13px",
};

function summary(overrides: Partial<DynamicWidgetSummary> = {}): DynamicWidgetSummary {
  return {
    id: "weather",
    title: "Weather",
    description: "Berlin, updated every 15 minutes",
    view: "stat",
    has_command: true,
    every_seconds: 900,
    has_data: true,
    updated_at_ms: 1_700_000_000_000,
    next_run_at_ms: null,
    error: null,
    ...overrides,
  };
}

function snapshot(
  overrides: Partial<DynamicWidgetSnapshot> = {},
  widget: Partial<DynamicWidgetSummary> = {},
): DynamicWidgetSnapshot {
  return { widget: summary(widget), data: { value: "21°C", label: "Berlin" }, html: null, ...overrides };
}

function fakeClient() {
  const listeners = new Set<(frame: AgentFrame) => void>();
  const ready = new Set<() => void>();
  const client = {
    onEvent: (callback: (frame: AgentFrame) => void) => {
      listeners.add(callback);
      return () => { listeners.delete(callback); };
    },
    onReady: (callback: () => void) => {
      ready.add(callback);
      return () => { ready.delete(callback); };
    },
  } as unknown as YoreniClient;
  return {
    client,
    emit: (frame: AgentFrame) => { listeners.forEach((callback) => callback(frame)); },
    reconnect: () => { ready.forEach((callback) => callback()); },
  };
}

function wrap(client: YoreniClient, capabilities: string[] = []) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <ClientProvider client={client} token="test" webuiCapabilities={capabilities}>
        {children}
      </ClientProvider>
    );
  };
}

function layout(): HomeWidgetsPayload {
  return {
    order: ["weather"],
    widgets: { weather: { visible: true, collapsed: false } },
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  resetHomeWidgets();
  vi.mocked(fetchGoal).mockResolvedValue({ active: false });
  vi.mocked(fetchMemoryBackfill).mockResolvedValue({ active: false });
  vi.mocked(fetchAutomations).mockResolvedValue({ jobs: [] });
  vi.mocked(fetchHomeWidgets).mockResolvedValue(layout());
  vi.mocked(fetchWidgets).mockResolvedValue({ widgets: [summary()] });
  vi.mocked(fetchWidget).mockResolvedValue(snapshot());
  vi.mocked(refreshWidget).mockResolvedValue({ widget_id: "weather", data: {}, duration_ms: 12 });
  vi.mocked(deleteWidget).mockResolvedValue({ deleted: "weather" });
  vi.mocked(saveHomeWidgets).mockImplementation(async (_transport, next) => next);
});

describe("widget document", () => {
  it("locks the frame down and hands the fragment its data", () => {
    const document = widgetDocument({
      html: "<p>hello</p>",
      data: { value: "21°C" },
      updatedAt: 1_700_000_000_000,
      theme: THEME,
    });

    expect(document).toContain("default-src 'none'");
    expect(document).toContain("connect-src 'none'");
    expect(document).toContain("script-src 'unsafe-inline'");
    expect(document).toContain("--widget-foreground: #111111");
    expect(document).toContain("<p>hello</p>");
    expect(document).toContain("window.yoreniWidget");
    expect(document).toContain("21°C");
  });

  it("escapes markup so data cannot close the bootstrap script", () => {
    const document = widgetDocument({
      html: "<p>hi</p>",
      data: { evil: "</script><script>alert(1)</script>" },
      updatedAt: null,
      theme: THEME,
    });

    expect(document).not.toContain("</script><script>alert(1)</script>");
    expect(document).toContain("\\u003c/script");
  });

  it("keeps the reported height inside the frame limits", () => {
    expect(clampWidgetHeight(120)).toBe(120);
    expect(clampWidgetHeight(4)).toBe(MIN_WIDGET_HEIGHT);
    expect(clampWidgetHeight(4000)).toBe(MAX_WIDGET_HEIGHT);
    expect(clampWidgetHeight("tall")).toBe(168);
  });

  it("measures content height and re-measures when the frame resizes", () => {
    const document = widgetDocument({
      html: "<p>hello</p>",
      data: {},
      updatedAt: null,
      theme: THEME,
    });

    expect(document).toContain("body.scrollHeight");
    expect(document).toContain('window.addEventListener("resize", measure)');
  });
});

describe("dynamic widget", () => {
  it("renders template data from the gateway snapshot", async () => {
    const { client } = fakeClient();
    render(<DynamicWidget id="weather" summary={summary()} />, { wrapper: wrap(client) });

    expect(await screen.findByText("21°C")).toBeInTheDocument();
    expect(screen.getByText("Berlin")).toBeInTheDocument();
    expect(fetchWidget).toHaveBeenCalledWith("test", "weather");
  });

  it("reloads when the gateway announces a change for this widget", async () => {
    const { client, emit } = fakeClient();
    render(<DynamicWidget id="weather" summary={summary()} />, { wrapper: wrap(client) });
    await screen.findByText("21°C");
    vi.mocked(fetchWidget).mockResolvedValue(
      snapshot({ data: { value: "18°C" } }, { updated_at_ms: 1_700_000_100_000 }),
    );

    act(() => emit({ event: "widget_changed", widget_id: "weather", kind: "data" }));

    expect(await screen.findByText("18°C")).toBeInTheDocument();
  });

  it("shows one muted row when the last refresh failed", async () => {
    const { client } = fakeClient();
    vi.mocked(fetchWidget).mockResolvedValue(
      snapshot({ data: null }, { error: "command exited with status 1", has_data: false }),
    );
    render(<DynamicWidget id="weather" summary={summary()} />, { wrapper: wrap(client) });

    expect(await screen.findByText("Couldn't refresh this widget.")).toBeInTheDocument();
    expect(screen.getByText("command exited with status 1")).toBeInTheDocument();
  });

  it("renders HTML widgets in an opaque-origin frame", async () => {
    const { client } = fakeClient();
    vi.mocked(fetchWidget).mockResolvedValue(
      snapshot({ data: { value: 4 }, html: "<p>hello</p>" }, { view: "html" }),
    );
    render(<DynamicWidget id="weather" summary={summary({ view: "html" })} />, { wrapper: wrap(client) });

    const frame = await screen.findByTitle("Weather");
    expect(frame.tagName).toBe("IFRAME");
    expect(frame).toHaveAttribute("sandbox", WIDGET_FRAME_SANDBOX);
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(frame).toHaveAttribute("referrerpolicy", "no-referrer");
    expect(frame).toHaveStyle({ backgroundColor: "transparent" });
  });

  it("replaces the frame when the widget switches to a template view", async () => {
    const { client, emit } = fakeClient();
    vi.mocked(fetchWidget).mockResolvedValue(
      snapshot({ data: { value: 4 }, html: "<p>hello</p>" }, { view: "html" }),
    );
    render(<DynamicWidget id="weather" summary={summary({ view: "html" })} />, { wrapper: wrap(client) });
    expect(await screen.findByTitle("Weather")).toBeInTheDocument();

    vi.mocked(fetchWidget).mockResolvedValue(snapshot({}, { view: "stat" }));
    act(() => emit({ event: "widget_changed", widget_id: "weather", kind: "updated" }));

    expect(await screen.findByText("21°C")).toBeInTheDocument();
    expect(screen.queryByTitle("Weather")).not.toBeInTheDocument();
  });

  it("renders a metrics grid of labelled values", async () => {
    const { client } = fakeClient();
    vi.mocked(fetchWidget).mockResolvedValue(
      snapshot(
        {
          data: {
            items: [
              { label: "今日剩余", value: "100%" },
              { label: "每日重置", value: "5:03 AM" },
            ],
          },
        },
        { view: "metrics" },
      ),
    );
    render(<DynamicWidget id="weather" summary={summary({ view: "metrics" })} />, {
      wrapper: wrap(client),
    });

    expect(await screen.findByText("今日剩余")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText("5:03 AM")).toBeInTheDocument();
  });

  it("renders a panel with a lead, blocks, and a note", async () => {
    const { client } = fakeClient();
    vi.mocked(fetchWidget).mockResolvedValue(
      snapshot(
        {
          data: {
            lead: "Hark Pro (free)",
            blocks: [
              { kind: "metrics", items: [{ label: "今日剩余", value: "100%" }] },
              {
                kind: "progress",
                title: "Storage",
                items: [{ label: "Drive", value: 62, detail: "62 GB" }],
              },
              { kind: "facts", title: "Facts", items: [{ label: "Region", value: "eu-west-1" }] },
              {
                kind: "chart",
                title: "Traffic",
                items: [
                  { label: "Mon", value: 3 },
                  { label: "Tue", value: 5 },
                ],
              },
              { kind: "timeline", title: "Deploys", items: [{ title: "api", meta: "12:40" }] },
              { kind: "badges", title: "Flags", items: [{ label: "beta" }] },
              {
                kind: "links",
                title: "Links",
                items: [{ label: "Runbook", href: "https://example.com/run" }],
              },
              { kind: "divider" },
            ],
            note: "Refreshed every 5 minutes",
          },
        },
        { view: "panel" },
      ),
    );
    render(<DynamicWidget id="weather" summary={summary({ view: "panel" })} />, {
      wrapper: wrap(client),
    });

    expect(await screen.findByText("Hark Pro (free)")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Drive" })).toHaveAttribute(
      "aria-valuenow",
      "62",
    );
    expect(screen.getByText("eu-west-1")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Mon 3, Tue 5" })).toBeInTheDocument();
    expect(screen.getByText("api")).toBeInTheDocument();
    expect(screen.getByText("beta")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Runbook/ })).toHaveAttribute(
      "href",
      "https://example.com/run",
    );
    expect(screen.getByText("Refreshed every 5 minutes")).toBeInTheDocument();
  });

  it("renders nothing until it has data", async () => {
    const { client } = fakeClient();
    vi.mocked(fetchWidget).mockResolvedValue(snapshot({ data: null }, { has_data: false }));
    const { container } = render(<DynamicWidget id="weather" summary={summary()} />, {
      wrapper: wrap(client),
    });

    await waitFor(() => expect(fetchWidget).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });
});

describe("home feed with dynamic widgets", () => {
  it("renders a gateway widget from the stored layout", async () => {
    const { client } = fakeClient();
    render(<HomeFeed />, { wrapper: wrap(client, ["webui.widgets.v1"]) });

    expect(await screen.findByText("21°C")).toBeInTheDocument();
    expect(fetchWidgets).toHaveBeenCalled();
  });

  it("adds a widget created while Home is open", async () => {
    const { client, emit } = fakeClient();
    render(<HomeFeed />, { wrapper: wrap(client, ["webui.widgets.v1"]) });
    await screen.findByText("21°C");

    vi.mocked(fetchWidgets).mockResolvedValue({
      widgets: [summary(), summary({ id: "deploys", title: "Deploys", view: "list" })],
    });
    vi.mocked(fetchWidget).mockImplementation(async (_transport, id) =>
      id === "deploys"
        ? snapshot(
          { data: { items: [{ title: "api", meta: "healthy" }] } },
          { id: "deploys", title: "Deploys", view: "list" },
        )
        : snapshot(),
    );

    act(() => emit({ event: "widget_changed", widget_id: "deploys", kind: "created" }));

    expect(await screen.findByText("Deploys")).toBeInTheDocument();
    expect(await screen.findByText("api")).toBeInTheDocument();
  });

  it("applies a renamed definition without a reload", async () => {
    const { client, emit } = fakeClient();
    render(<HomeFeed />, { wrapper: wrap(client, ["webui.widgets.v1"]) });
    await screen.findByText("21°C");

    vi.mocked(fetchWidgets).mockResolvedValue({ widgets: [summary({ title: "Berlin weather" })] });
    vi.mocked(fetchWidget).mockResolvedValue(snapshot({}, { title: "Berlin weather" }));

    act(() => emit({ event: "widget_changed", widget_id: "weather", kind: "updated" }));

    expect(await screen.findByText("Berlin weather")).toBeInTheDocument();
  });

  it("rereads widgets after the client reconnects", async () => {
    const { client, reconnect } = fakeClient();
    render(<HomeFeed />, { wrapper: wrap(client, ["webui.widgets.v1"]) });
    await screen.findByText("21°C");

    vi.mocked(fetchWidgets).mockResolvedValue({
      widgets: [summary(), summary({ id: "deploys", title: "Deploys" })],
    });
    vi.mocked(fetchWidget).mockImplementation(async (_transport, id) =>
      id === "deploys"
        ? snapshot({ data: { value: 3 } }, { id: "deploys", title: "Deploys" })
        : snapshot(),
    );

    act(() => reconnect());

    expect(await screen.findByText("Deploys")).toBeInTheDocument();
  });

  it("removes a widget deleted while Home is open", async () => {
    const { client, emit } = fakeClient();
    render(<HomeFeed />, { wrapper: wrap(client, ["webui.widgets.v1"]) });
    await screen.findByText("21°C");

    vi.mocked(fetchWidgets).mockResolvedValue({ widgets: [] });

    act(() => emit({ event: "widget_changed", widget_id: "weather", kind: "deleted" }));

    await waitFor(() => expect(screen.queryByText("21°C")).not.toBeInTheDocument());
  });

  it("keeps the built-in widgets when the capability is absent", async () => {
    const { client } = fakeClient();
    render(<HomeFeed />, { wrapper: wrap(client) });

    await waitFor(() => expect(fetchHomeWidgets).toHaveBeenCalled());
    expect(fetchWidgets).not.toHaveBeenCalled();
    expect(fetchWidget).not.toHaveBeenCalled();
  });
});

describe("dynamic widget settings", () => {
  it("lists a gateway widget with refresh and delete actions", async () => {
    const { client } = fakeClient();
    render(<WidgetsSettings />, { wrapper: wrap(client, ["webui.widgets.v1"]) });

    expect(await screen.findByText("Weather")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh Weather" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete Weather" })).toBeInTheDocument();
  });

  it("refreshes a widget from the settings row", async () => {
    const user = userEvent.setup();
    const { client } = fakeClient();
    render(<WidgetsSettings />, { wrapper: wrap(client, ["webui.widgets.v1"]) });

    await user.click(await screen.findByRole("button", { name: "Refresh Weather" }));

    await waitFor(() => expect(refreshWidget).toHaveBeenCalledWith(expect.anything(), "weather"));
  });

  it("deletes a widget after confirmation", async () => {
    const user = userEvent.setup();
    const { client } = fakeClient();
    render(<WidgetsSettings />, { wrapper: wrap(client, ["webui.widgets.v1"]) });

    await user.click(await screen.findByRole("button", { name: "Delete Weather" }));
    expect(await screen.findByText("Delete Weather?")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Delete widget" }));

    await waitFor(() => expect(deleteWidget).toHaveBeenCalledWith(expect.anything(), "weather"));
  });
});
