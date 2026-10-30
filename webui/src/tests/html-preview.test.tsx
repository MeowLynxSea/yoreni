import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HtmlAttachment } from "@/components/HtmlAttachment";

describe("html preview", () => {
  it("gives the whole dialog body to the page", async () => {
    render(<HtmlAttachment attachment={{ kind: "html", name: "page.html", url: "data:text/html,<p>page</p>" }} />);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Preview HTML page" }));
    });

    const dialog = await screen.findByRole("dialog");
    const frame = dialog.querySelector("iframe");

    // The title row stays its own height; the page takes every remaining pixel
    // instead of sharing the grid rows with the title.
    expect(dialog).toHaveClass("grid-rows-[auto_minmax(0,1fr)]");
    expect(frame).toHaveClass("h-full", "min-h-0");
  });
});
