import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ThreadComposer } from "@/components/thread/ThreadComposer";

const PNG_BYTES = Uint8Array.from(atob(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
), (character) => character.charCodeAt(0));

function imageFile(name = "photo.png"): File {
  return new File([PNG_BYTES], name, { type: "image/png" });
}

async function stage(container: HTMLElement, file: File): Promise<void> {
  const input = container.querySelector<HTMLInputElement>('input[type="file"]');
  if (!input) throw new Error("composer has no file input");
  await userEvent.setup().upload(input, file);
}

describe("composer attachment previews", () => {
  it("shows an attached image as its own picture instead of a file name", async () => {
    const { container } = render(<ThreadComposer onSend={vi.fn()} />);
    await stage(container, imageFile());

    await waitFor(() => expect(container.querySelector('[data-composer-attachment="image"]')).not.toBeNull());
    const chip = container.querySelector('[data-composer-attachment="image"]') as HTMLElement;
    expect(chip.querySelector("img")?.getAttribute("src")).toMatch(/^blob:/);
    expect(screen.queryByText("photo.png")).not.toBeInTheDocument();
  });

  it("labels a document with its name and size and shortens a long one", async () => {
    const { container } = render(<ThreadComposer onSend={vi.fn()} />);
    const longName = `${"quarterly-report-".repeat(4)}final.pdf`;
    await stage(container, new File(["hello"], longName, { type: "application/pdf" }));

    await waitFor(() => expect(container.querySelector('[data-composer-attachment="file"]')).not.toBeNull());
    const chip = container.querySelector('[data-composer-attachment="file"]') as HTMLElement;
    expect(chip).toHaveTextContent("5 B");
    const shown = chip.querySelector(`[title="${longName}"]`);
    expect(shown?.textContent).toContain("…");
    expect(shown?.textContent?.endsWith(".pdf")).toBe(true);
    expect(shown?.textContent?.length).toBeLessThanOrEqual(24);
  });

  it("accepts a file whose type the browser does not know", async () => {
    const { container } = render(<ThreadComposer onSend={vi.fn()} />);
    await stage(container, new File([new Uint8Array([1, 2, 3])], "weights.gguf", { type: "" }));

    await waitFor(() => expect(container.querySelector('[data-composer-attachment="file"]')).not.toBeNull());
    const chip = container.querySelector('[data-composer-attachment="file"]') as HTMLElement;
    expect(chip.querySelector('[title="weights.gguf"]')).toHaveTextContent("weights.gguf");
  });

  it("hands the staged preview to the sent message and can discard it", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn().mockResolvedValue(undefined);
    const { container } = render(<ThreadComposer onSend={onSend} />);
    await stage(container, imageFile("chart.png"));
    await waitFor(() => expect(container.querySelector('[data-composer-attachment="image"]')).not.toBeNull());

    await user.type(screen.getByRole("textbox"), "look");
    await user.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(onSend).toHaveBeenCalled());
    const [content, attachments] = onSend.mock.calls[0];
    expect(content).toBe("look");
    expect(attachments[0].media).toMatchObject({ name: "chart.png" });
    expect(attachments[0].preview).toMatchObject({ kind: "image", name: "chart.png", size: PNG_BYTES.length });
    // The sent bubble outlives the composer's blob URL, so it keeps the encoded copy.
    expect(attachments[0].preview.url).toMatch(/^data:image\/png;base64,/);
  });

  it("removes a staged attachment from the composer", async () => {
    const user = userEvent.setup();
    const { container } = render(<ThreadComposer onSend={vi.fn()} />);
    await stage(container, imageFile());
    await waitFor(() => expect(container.querySelector("[data-composer-attachment]")).not.toBeNull());

    await user.click(screen.getByRole("button", { name: "Remove attachment" }));

    expect(container.querySelector("[data-composer-attachment]")).toBeNull();
  });
});
