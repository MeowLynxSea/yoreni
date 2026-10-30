import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ChatSearch } from "@/components/ChatSearch";
import {
  parseChatSearchResult,
  parseChatSemanticResult,
  type ChatSearchHit,
  type ChatSearchLocalResult,
} from "@/lib/chat-search";

function hit(
  id: string,
  excerpt: string,
  role: ChatSearchHit["role"] = "user",
  spans: Array<[number, number]> = [],
  createdAt = 1,
): ChatSearchHit {
  return { id, role, excerpt, spans, createdAt };
}

describe("chat search payloads", () => {
  it("keeps well-formed hits and drops spans outside the excerpt", () => {
    const parsed = parseChatSearchResult({
      exact: [
        { id: "a", role: "user", excerpt: "alpha beta", spans: [[0, 5], [4, 99]], created_at_ms: 7 },
        { id: "", role: "user", excerpt: "no id" },
        { id: "b", role: "system", excerpt: "wrong role" },
        "not a hit",
      ],
      fuzzy: "not a list",
    });

    expect(parsed.exact).toEqual([{ id: "a", role: "user", excerpt: "alpha beta", spans: [[0, 5]], createdAt: 7 }]);
    expect(parsed.fuzzy).toEqual([]);
  });

  it("reads semantic hits from the hits field", () => {
    expect(parseChatSemanticResult({ hits: [{ id: "s", role: "assistant", excerpt: "related" }] }))
      .toEqual([{ id: "s", role: "assistant", excerpt: "related", spans: [], createdAt: 0 }]);
    expect(parseChatSemanticResult(null)).toEqual([]);
  });
});

describe("chat search overlay", () => {
  function renderSearch(options: {
    exact?: ChatSearchHit[];
    fuzzy?: ChatSearchHit[];
    semantic?: ChatSearchHit[];
    requestLocal?: (query: string) => Promise<ChatSearchLocalResult>;
  } = {}) {
    const onOpenChange = vi.fn();
    const onSelect = vi.fn();
    const requestLocal = vi.fn(options.requestLocal
      ?? (async () => ({ exact: options.exact ?? [], fuzzy: options.fuzzy ?? [] })));
    const requestSemantic = vi.fn(async () => options.semantic ?? []);
    render(
      <ChatSearch
        open
        onOpenChange={onOpenChange}
        onSelect={onSelect}
        requestLocal={requestLocal}
        requestSemantic={requestSemantic}
      />,
    );
    return { onOpenChange, onSelect, requestLocal, requestSemantic };
  }

  it("shows server exact matches and jumps to the chosen hit", async () => {
    const user = userEvent.setup();
    const exact = hit("m1", "How do I deploy the gateway?", "user", [[9, 15]]);
    const { onSelect, onOpenChange, requestLocal } = renderSearch({ exact: [exact] });

    await user.type(screen.getByRole("textbox", { name: "Search chat history" }), "deploy");

    expect(await screen.findByRole("heading", { name: "Exact match" })).toBeInTheDocument();
    expect(screen.getByText("deploy", { selector: "mark" })).toBeInTheDocument();
    expect(requestLocal).toHaveBeenCalledWith("deploy");

    await user.keyboard("{Enter}");

    expect(onSelect).toHaveBeenCalledWith(exact);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("appends semantic matches after the local tiers and drops duplicates", async () => {
    const user = userEvent.setup();
    const duplicate = hit("m1", "Deployment notes", "assistant");
    const { requestSemantic } = renderSearch({
      exact: [duplicate],
      semantic: [hit("m2", "Run the install script, then restart.", "assistant"), hit("m1", "Deployment notes")],
    });

    await user.type(screen.getByRole("textbox", { name: "Search chat history" }), "deploy");

    expect(await screen.findByRole("heading", { name: "Semantic match" })).toBeInTheDocument();
    expect(requestSemantic).toHaveBeenCalledWith("deploy");
    expect(screen.getByText("Run the install script, then restart.")).toBeInTheDocument();
    expect(screen.getAllByText("Deployment notes")).toHaveLength(1);
  });

  it("ignores a response for a query the reader already replaced", async () => {
    const user = userEvent.setup();
    let resolveFirst: (result: ChatSearchLocalResult) => void = () => {};
    const { requestLocal } = renderSearch({
      requestLocal: (query) => query === "ga"
        ? new Promise((resolve) => { resolveFirst = resolve; })
        : Promise.resolve({ exact: [hit("later", "gateway restart")], fuzzy: [] }),
    });

    await user.type(screen.getByRole("textbox", { name: "Search chat history" }), "ga");
    await waitFor(() => expect(requestLocal).toHaveBeenCalledWith("ga"));
    await user.clear(screen.getByRole("textbox", { name: "Search chat history" }));
    await user.type(screen.getByRole("textbox", { name: "Search chat history" }), "gateway");

    expect(await screen.findByText("gateway restart")).toBeInTheDocument();
    resolveFirst({ exact: [hit("stale", "old match")], fuzzy: [] });
    await waitFor(() => expect(screen.queryByText("old match")).not.toBeInTheDocument());
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    const { onOpenChange } = renderSearch();

    await user.type(screen.getByRole("textbox", { name: "Search chat history" }), "deploy");
    await user.keyboard("{Escape}");

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("says when nothing matches", async () => {
    const user = userEvent.setup();
    renderSearch();

    await user.type(screen.getByRole("textbox", { name: "Search chat history" }), "zzzzzz");

    expect(await screen.findByText("No matching messages.")).toBeInTheDocument();
  });

  it("says when the gateway cannot search", async () => {
    const user = userEvent.setup();
    renderSearch({ requestLocal: async () => { throw new Error("offline"); } });

    await user.type(screen.getByRole("textbox", { name: "Search chat history" }), "deploy");

    expect(await screen.findByText("Search is unavailable right now.")).toBeInTheDocument();
  });
});
