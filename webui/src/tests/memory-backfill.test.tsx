import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryBackfillWidget } from "@/components/home/MemoryBackfillWidget";
import { resetHomeWidgets } from "@/components/home/widget-layout";
import { WidgetsSettings } from "@/components/settings/overview/WidgetsSettings";
import {
  fetchAutomations,
  fetchGoal,
  fetchHomeWidgets,
  fetchMemoryBackfill,
  saveHomeWidgets,
  setMemoryBackfillPaused,
} from "@/lib/api";
import type { HomeWidgetsPayload, MemoryBackfillPayload } from "@/lib/types";
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

function fakeClient() {
  const listeners = new Set<(frame: AgentFrame) => void>();
  const client = {
    onEvent: (callback: (frame: AgentFrame) => void) => {
      listeners.add(callback);
      return () => { listeners.delete(callback); };
    },
    onReady: () => () => {},
    requestMutation: vi.fn(),
  } as unknown as YoreniClient;
  return {
    client,
    emit: (frame: AgentFrame) => { listeners.forEach((callback) => callback(frame)); },
  };
}

function wrap(client: YoreniClient) {
  return ({ children }: { children: ReactNode }) => (
    <ClientProvider client={client} token="test">{children}</ClientProvider>
  );
}

function layout(): HomeWidgetsPayload {
  return {
    order: ["memory-backfill", "goal", "automations"],
    widgets: {
      goal: { visible: true, collapsed: false },
      automations: { visible: true, collapsed: false },
    },
  };
}

const RUNNING: MemoryBackfillPayload = {
  active: true,
  status: "running",
  model: "bge-m3",
  done: 3,
  total: 10,
  remaining: 7,
  eta_ms: 42_000,
};

beforeEach(() => {
  window.localStorage.clear();
  resetHomeWidgets();
  vi.mocked(fetchGoal).mockResolvedValue({ active: false });
  vi.mocked(fetchAutomations).mockResolvedValue({ jobs: [] });
  vi.mocked(fetchHomeWidgets).mockResolvedValue(layout());
  vi.mocked(fetchMemoryBackfill).mockResolvedValue({ active: false });
  vi.mocked(setMemoryBackfillPaused).mockResolvedValue({ ...RUNNING, status: "paused" });
  vi.mocked(saveHomeWidgets).mockImplementation(async (_transport, next) => next);
});

describe("memory update widget", () => {
  it("stays out of the way when there is nothing to do", async () => {
    const { client } = fakeClient();
    render(<MemoryBackfillWidget />, { wrapper: wrap(client) });

    await waitFor(() => expect(fetchMemoryBackfill).toHaveBeenCalled());
    expect(screen.queryByText("Memory update")).not.toBeInTheDocument();
  });

  it("shows progress and follows live updates", async () => {
    vi.mocked(fetchMemoryBackfill).mockResolvedValue(RUNNING);
    const { client, emit } = fakeClient();
    render(<MemoryBackfillWidget />, { wrapper: wrap(client) });

    expect(await screen.findByText("3 of 10 memories")).toBeInTheDocument();
    expect(screen.getByText("Updating")).toBeInTheDocument();
    expect(screen.getByText(/^About .+ left$/)).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "30");

    act(() => emit({
      event: "memory_backfill",
      memory_backfill: { ...RUNNING, status: "paused" },
    }));

    expect(await screen.findByText("Paused")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("disappears once the work is finished", async () => {
    vi.mocked(fetchMemoryBackfill).mockResolvedValue(RUNNING);
    const { client, emit } = fakeClient();
    render(<MemoryBackfillWidget />, { wrapper: wrap(client) });
    expect(await screen.findByText("3 of 10 memories")).toBeInTheDocument();

    act(() => emit({ event: "memory_backfill", memory_backfill: { active: false } }));

    await waitFor(() => expect(screen.queryByText("Memory update")).not.toBeInTheDocument());
  });

  it("pauses the work", async () => {
    vi.mocked(fetchMemoryBackfill).mockResolvedValue(RUNNING);
    const user = userEvent.setup();
    const { client } = fakeClient();
    render(<MemoryBackfillWidget />, { wrapper: wrap(client) });

    await user.click(await screen.findByRole("button", { name: "Pause" }));

    expect(setMemoryBackfillPaused).toHaveBeenLastCalledWith(client, true);
  });

  it("resumes paused work", async () => {
    vi.mocked(fetchMemoryBackfill).mockResolvedValue({ ...RUNNING, status: "paused" });
    vi.mocked(setMemoryBackfillPaused).mockResolvedValue(RUNNING);
    const user = userEvent.setup();
    const { client } = fakeClient();
    render(<MemoryBackfillWidget />, { wrapper: wrap(client) });

    await user.click(await screen.findByRole("button", { name: "Resume" }));

    expect(setMemoryBackfillPaused).toHaveBeenLastCalledWith(client, false);
  });
});

describe("memory update settings", () => {
  it("cannot be switched off from settings", async () => {
    const { client } = fakeClient();
    render(<WidgetsSettings />, { wrapper: wrap(client) });

    expect(await screen.findByText("Memory update")).toBeInTheDocument();
    expect(screen.queryByRole("switch", { name: "Memory update" })).not.toBeInTheDocument();
    expect(screen.getByText("Automatic")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Goal" })).toBeInTheDocument();
  });
});
