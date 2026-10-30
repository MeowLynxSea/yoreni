import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HomeFeed } from "@/components/home/HomePanel";
import { GoalWidget } from "@/components/home/GoalWidget";
import { AutomationsWidget } from "@/components/home/AutomationsWidget";
import { resetHomeWidgets } from "@/components/home/widget-layout";
import { WidgetsSettings } from "@/components/settings/overview/WidgetsSettings";
import { fetchAutomations, fetchGoal, fetchHomeWidgets, saveHomeWidgets } from "@/lib/api";
import type { AutomationJob, HomeWidgetsPayload } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";
import type { AgentFrame, YoreniClient } from "@/lib/yoreni-client";

vi.mock("@/lib/api", () => ({
  deleteWidget: vi.fn(),
  fetchGoal: vi.fn(),
  fetchAutomations: vi.fn(),
  fetchHomeWidgets: vi.fn(),
  fetchWidget: vi.fn(),
  fetchWidgets: vi.fn(),
  refreshWidget: vi.fn(),
  saveHomeWidgets: vi.fn(),
}));

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
  return { client, emit: (frame: AgentFrame) => { listeners.forEach((callback) => callback(frame)); } };
}

function wrap(client: YoreniClient) {
  return ({ children }: { children: ReactNode }) => (
    <ClientProvider client={client} token="test">{children}</ClientProvider>
  );
}

function layout(overrides: Partial<HomeWidgetsPayload> = {}): HomeWidgetsPayload {
  return {
    order: ["goal", "automations"],
    widgets: {
      goal: { visible: true, collapsed: false },
      automations: { visible: true, collapsed: false },
    },
    ...overrides,
  };
}

function job(overrides: Partial<AutomationJob> = {}): AutomationJob {
  return {
    id: "job-1",
    name: "Morning briefing",
    enabled: true,
    kind: "cron",
    schedule: { kind: "cron", expr: "0 9 * * *" },
    payload: { message: "Summarize the day" },
    state: { next_run_at_ms: Date.now() + 3_600_000 },
    ...overrides,
  } as AutomationJob;
}

beforeEach(() => {
  window.localStorage.clear();
  resetHomeWidgets();
  vi.mocked(fetchGoal).mockResolvedValue({ active: false });
  vi.mocked(fetchAutomations).mockResolvedValue({ jobs: [] });
  vi.mocked(fetchHomeWidgets).mockResolvedValue(layout());
  vi.mocked(saveHomeWidgets).mockImplementation(async (_transport, next) => next);
});

describe("home widgets", () => {
  it("shows the open goal and follows runtime goal updates", async () => {
    vi.mocked(fetchGoal).mockResolvedValue({
      active: true, status: "active", ui_summary: "Ship the fix", objective: "Ship the fix and verify tests.",
    });
    const { client, emit } = fakeClient();
    render(<GoalWidget />, { wrapper: wrap(client) });

    expect(await screen.findByText("Ship the fix")).toBeInTheDocument();
    expect(screen.getByText("In progress")).toBeInTheDocument();

    act(() => emit({
      event: "goal_state",
      goal_state: { active: false, status: "blocked", recap: "Needs production access." },
    }));

    expect(await screen.findByText("Blocked")).toBeInTheDocument();
    expect(screen.getByText("Needs production access.")).toBeInTheDocument();
  });

  it("hides the goal widget once the goal is cleared", async () => {
    vi.mocked(fetchGoal).mockResolvedValue({
      active: true, status: "active", ui_summary: "Ship the fix", objective: "Ship the fix and verify tests.",
    });
    const { client, emit } = fakeClient();
    render(<GoalWidget />, { wrapper: wrap(client) });
    expect(await screen.findByText("Ship the fix")).toBeInTheDocument();

    act(() => emit({ event: "goal_state", goal_state: { active: false } }));

    await waitFor(() => expect(screen.queryByText("Goal")).not.toBeInTheDocument());
  });

  it("hides the automations widget when nothing is scheduled", async () => {
    vi.mocked(fetchAutomations).mockResolvedValue({ jobs: [job()] });
    const { client } = fakeClient();
    render(<AutomationsWidget />, { wrapper: wrap(client) });
    expect(await screen.findByText("Morning briefing")).toBeInTheDocument();

    vi.mocked(fetchAutomations).mockResolvedValue({ jobs: [] });
    act(() => { window.dispatchEvent(new Event("focus")); });

    await waitFor(() => expect(screen.queryByText("Automations")).not.toBeInTheDocument());
  });

  it("shows the empty state until a widget has content", async () => {
    const { client, emit } = fakeClient();
    render(<HomeFeed />, { wrapper: wrap(client) });

    expect(await screen.findByText("Nothing here yet")).toBeInTheDocument();

    act(() => emit({
      event: "goal_state",
      goal_state: { active: true, status: "active", ui_summary: "Ship the fix", objective: "Ship the fix and verify tests." },
    }));

    await waitFor(() => expect(screen.queryByText("Nothing here yet")).not.toBeInTheDocument());
  });

  it("lists the next scheduled task", async () => {
    vi.mocked(fetchAutomations).mockResolvedValue({ jobs: [job()] });
    const { client } = fakeClient();
    render(<AutomationsWidget />, { wrapper: wrap(client) });

    expect(await screen.findByText("Morning briefing")).toBeInTheDocument();
  });

  it("renders widgets in the stored order and skips the hidden ones", async () => {
    vi.mocked(fetchAutomations).mockResolvedValue({ jobs: [job()] });
    vi.mocked(fetchGoal).mockResolvedValue({
      active: true, status: "active", ui_summary: "Ship the fix", objective: "Ship the fix.",
    });
    vi.mocked(fetchHomeWidgets).mockResolvedValue(layout({
      order: ["automations", "goal"],
      widgets: {
        goal: { visible: false, collapsed: false },
        automations: { visible: true, collapsed: false },
      },
    }));
    const { client } = fakeClient();
    render(<HomeFeed />, { wrapper: wrap(client) });

    expect(await screen.findByText("Morning briefing")).toBeInTheDocument();
    expect(screen.queryByText("Ship the fix")).not.toBeInTheDocument();
  });

  it("collapses a widget and stores the collapsed state", async () => {
    vi.mocked(fetchAutomations).mockResolvedValue({ jobs: [job()] });
    const user = userEvent.setup();
    const { client } = fakeClient();
    render(<HomeFeed />, { wrapper: wrap(client) });

    const header = await screen.findByRole("button", { name: "Automations" });
    expect(header).toHaveAttribute("aria-expanded", "true");

    await user.click(header);

    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Morning briefing")).toBeInTheDocument();
    await waitFor(() => expect(saveHomeWidgets).toHaveBeenLastCalledWith(
      expect.anything(),
      expect.objectContaining({
        widgets: expect.objectContaining({
          automations: { visible: true, collapsed: true },
        }),
      }),
    ));
  });
});

describe("widget settings", () => {
  it("toggles a widget for the installation", async () => {
    const user = userEvent.setup();
    const { client } = fakeClient();
    render(<WidgetsSettings />, { wrapper: wrap(client) });

    const toggle = await screen.findByRole("switch", { name: "Goal" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "false");

    await waitFor(() => expect(saveHomeWidgets).toHaveBeenLastCalledWith(
      expect.anything(),
      expect.objectContaining({
        widgets: expect.objectContaining({ goal: { visible: false, collapsed: false } }),
      }),
    ));
  });

  it("moves a widget down in the stored order", async () => {
    const user = userEvent.setup();
    const { client } = fakeClient();
    render(<WidgetsSettings />, { wrapper: wrap(client) });

    await user.click(await screen.findByRole("button", { name: "Move Goal down" }));

    await waitFor(() => expect(saveHomeWidgets).toHaveBeenLastCalledWith(
      expect.anything(),
      expect.objectContaining({ order: ["automations", "goal"] }),
    ));
  });
});
