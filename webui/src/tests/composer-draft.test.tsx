import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ThreadComposer } from "@/components/thread/ThreadComposer";

const PLACEHOLDER = "Type your message…";
const DRAFT_KEY = "yoreni-webui.composer-draft";

describe("composer draft persistence", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("restores an unsent draft after the composer remounts", async () => {
    const user = userEvent.setup();
    const first = render(<ThreadComposer onSend={vi.fn()} persistDraft />);
    await user.type(screen.getByPlaceholderText(PLACEHOLDER), "unfinished thought");
    first.unmount();

    render(<ThreadComposer onSend={vi.fn()} persistDraft />);

    expect(screen.getByPlaceholderText(PLACEHOLDER)).toHaveValue("unfinished thought");
  });

  it("drops the draft once the message is accepted", async () => {
    const user = userEvent.setup();
    render(<ThreadComposer onSend={vi.fn().mockResolvedValue(undefined)} persistDraft />);
    await user.type(screen.getByPlaceholderText(PLACEHOLDER), "hello");

    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(screen.getByPlaceholderText(PLACEHOLDER)).toHaveValue(""));
    expect(localStorage.getItem(DRAFT_KEY)).toBeNull();
  });
});
