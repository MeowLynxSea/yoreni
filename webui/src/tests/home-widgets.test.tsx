import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HomeFeed } from "@/components/home/HomePanel";
import { GoalWidget } from "@/components/home/GoalWidget";
import { AutomationsWidget } from "@/components/home/AutomationsWidget";
import { WidgetsSettings } from "@/components/settings/overview/WidgetsSettings";
import { fetchAutomations, fetchGoal } from "@/lib/api";
import { DEFAULT_LOCAL_PREFS, writeLocalPreferences } from "@/lib/local-preferences";
import type { AutomationJob } from "@/lib/types";
import { ClientProvider } from "@/providers/ClientProvider";
import type { AgentFrame, YoreniClient } from "@/lib/yoreni-client";

vi.mock("@/lib/api", () => ({
  fetchGoal: vi.fn(),
  fetchAutomations: vi.fn(),
}));

function fakeClient() {
  const listeners = new Set<(frame: AgentFrame) => void>();
  const client = {
    onEvent: (callback: (frame: AgentFrame) => void) => {
      listeners.add(callback);
      return () => { listeners.delete(callback); };
    },
  } as unknown as YoreniClient;
  return { client, emit: (frame: AgentFrame) => { listeners.forEach((callback) => callback(frame)); } };
}

function wrap(client: YoreniClient) {
  return ({ children }: { children: ReactNode }) => (
    <ClientProvider client={client} token="test">{children}</ClientProvider>
  );
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
  vi.mocked(fetchGoal).mockResolvedValue({ active: false });
  vi.mocked(fetchAutomations).mockResolvedValue({ jobs: [] });
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

  it("lists the next scheduled task", async () => {
    vi.mocked(fetchAutomations).mockResolvedValue({ jobs: [job()] });
    const { client } = fakeClient();
    render(<AutomationsWidget />, { wrapper: wrap(client) });

    expect(await screen.findByText("Morning briefing")).toBeInTheDocument();
  });

  it("hides widgets that the device turned off", async () => {
    writeLocalPreferences({ ...DEFAULT_LOCAL_PREFS, homeWidgets: { goal: false } });
    const { client } = fakeClient();
    render(<HomeFeed />, { wrapper: wrap(client) });

    expect(screen.queryByText("Goal")).not.toBeInTheDocument();
    expect(await screen.findByText("Automations")).toBeInTheDocument();
  });
});

describe("widget settings", () => {
  it("toggles a system widget for this device", async () => {
    function Harness() {
      const [prefs, setPrefs] = useState({ ...DEFAULT_LOCAL_PREFS, homeWidgets: {} });
      return <WidgetsSettings localPrefs={prefs} onChangeLocalPrefs={setPrefs} />;
    }
    const user = userEvent.setup();
    render(<Harness />);

    const toggle = screen.getByRole("switch", { name: "Goal" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-checked", "false");
  });
});
