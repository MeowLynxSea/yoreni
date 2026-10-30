import { act, fireEvent, render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ImageLightbox } from "@/components/ImageLightbox";

function renderLightbox(onOpenChange = vi.fn()) {
  render(<ImageLightbox
    images={[{ url: "/api/media/a.png", name: "a.png" }]}
    index={0}
    onIndexChange={() => {}}
    onOpenChange={onOpenChange}
  />);
  const viewport = document.querySelector('[data-testid="image-zoom-viewport"]');
  return { onOpenChange, viewport: viewport as HTMLElement };
}

describe("image lightbox", () => {
  it("closes when the space around the photo is clicked", async () => {
    const { onOpenChange, viewport } = renderLightbox();

    await act(async () => { fireEvent.click(viewport); });

    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("stays open when the photo itself is clicked", async () => {
    const { onOpenChange, viewport } = renderLightbox();
    const image = viewport.querySelector("img");

    await act(async () => { fireEvent.click(image as HTMLImageElement); });

    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
