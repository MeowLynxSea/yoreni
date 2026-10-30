import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  installSettingsViewTestHooks,
  renderSettingsView,
  requestMutationMock,
  settingsPayload,
} from "@/tests/settings-test-utils";

describe("Settings memory", () => {
  installSettingsViewTestHooks();

  it("configures a custom embedding endpoint", async () => {
    const payload = settingsPayload();
    payload.memory = {
      ...payload.memory!,
      embedding: { ...payload.memory!.embedding, enabled: true },
    };
    requestMutationMock.mockResolvedValue({
      ...payload,
      memory: {
        ...payload.memory,
        embedding: {
          enabled: true,
          provider: null,
          model: "nomic-embed-text",
          api_base: "http://127.0.0.1:11434/v1",
          api_key_hint: "loca••••cret",
          dimensions: 768,
          batch_size: 32,
        },
      },
      requires_restart: true,
      restart_required_sections: ["runtime"],
    });
    renderSettingsView({ initialSection: "memory", initialSettings: payload });
    const memory = within(screen.getByRole("dialog"));

    fireEvent.change(memory.getByRole("textbox", { name: "API base" }), {
      target: { value: "http://127.0.0.1:11434/v1" },
    });
    fireEvent.change(memory.getByRole("textbox", { name: "Model" }), {
      target: { value: "nomic-embed-text" },
    });
    fireEvent.change(memory.getByLabelText("API key"), {
      target: { value: "local-secret" },
    });
    fireEvent.change(memory.getByRole("spinbutton", { name: "Dimensions" }), {
      target: { value: "768" },
    });
    fireEvent.blur(memory.getByRole("spinbutton", { name: "Dimensions" }));

    await waitFor(() => expect(requestMutationMock).toHaveBeenCalledWith(
      "settings.memory.update",
      expect.objectContaining({
        embedding_enabled: true,
        embedding_api_base: "http://127.0.0.1:11434/v1",
        embedding_model: "nomic-embed-text",
        embedding_api_key: "local-secret",
        embedding_dimensions: 768,
      }),
      20_000,
    ));
    expect(await screen.findByText("Saved. Restart to apply changes.")).toBeVisible();
  });

  it.each(["Image generation", "Transcription", "Web access", "Long-term memory"])(
    "renders the %s dialog as one surface instead of a card inside a card",
    (title) => {
      const payload = settingsPayload();
      payload.runtime_config = { "tools.web.enable": true, "agents.defaults.dream.enabled": true };
      renderSettingsView({ initialSection: "capabilities", initialSettings: payload });

      fireEvent.click(screen.getByRole("button", { name: title }));
      const dialog = screen.getByRole("dialog");
      expect(dialog.querySelector(".settings-sheet")).toBeNull();
      fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    },
  );
});
