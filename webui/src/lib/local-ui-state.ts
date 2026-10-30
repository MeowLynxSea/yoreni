/** Browser-local shell state that must survive a reload. */

export type HomeView = "chat" | "home";

const HOME_VIEW_KEY = "yoreni-webui.home-view";
const COMPOSER_DRAFT_KEY = "yoreni-webui.composer-draft";

export function readHomeView(): HomeView {
  try {
    return window.localStorage.getItem(HOME_VIEW_KEY) === "home" ? "home" : "chat";
  } catch {
    return "chat";
  }
}

export function writeHomeView(view: HomeView): void {
  try {
    window.localStorage.setItem(HOME_VIEW_KEY, view);
  } catch {
    // Restoring the last view is a convenience; storage failure must not block navigation.
  }
}

export function readComposerDraft(): string {
  try {
    return window.localStorage.getItem(COMPOSER_DRAFT_KEY) ?? "";
  } catch {
    return "";
  }
}

/** Drop the entry when the draft is empty so reloads never restore a blank composer. */
export function writeComposerDraft(text: string): void {
  try {
    if (text) window.localStorage.setItem(COMPOSER_DRAFT_KEY, text);
    else window.localStorage.removeItem(COMPOSER_DRAFT_KEY);
  } catch {
    // Draft retention is best-effort; sending must still work without storage.
  }
}
