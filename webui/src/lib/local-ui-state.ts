/** Browser-local shell state that must survive a reload. */

export type HomeView = "chat" | "home";

const HOME_VIEW_KEY = "yoreni-webui.home-view";
const COMPOSER_DRAFT_KEY = "yoreni-webui.composer-draft";
const LOCATION_KEY = "yoreni-webui.location";
const LAST_PROJECTS_PATH_KEY = "yoreni-webui.last-projects-path";
const PROJECTS_PATH_PATTERN = /^\/projects(?:\/[a-f0-9]{32})?$/;

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

/** The in-app path to reopen when the app starts on its root. */
export function readLastLocation(): string | null {
  try {
    const value = window.localStorage.getItem(LOCATION_KEY);
    return value && value.startsWith("/") ? value : null;
  } catch {
    return null;
  }
}

/**
 * The path to replay at startup. Only a bare start route is replaced, so a
 * deep link always wins over the remembered page.
 */
export function restoreLocation(current: string, stored: string | null): string | null {
  if (current !== "/" || !stored || stored === "/" || !stored.startsWith("/")) return null;
  return stored;
}

export function writeLastLocation(location: string): void {
  try {
    window.localStorage.setItem(LOCATION_KEY, location);
  } catch {
    // Reopening the last page is a convenience; storage failure must not block navigation.
  }
}

/** Where the reader left the projects view: the list, or one project. */
export function readLastProjectsPath(): string | null {
  try {
    const value = window.localStorage.getItem(LAST_PROJECTS_PATH_KEY);
    return value && PROJECTS_PATH_PATTERN.test(value) ? value : null;
  } catch {
    return null;
  }
}

export function writeLastProjectsPath(path: string | null): void {
  try {
    if (path && PROJECTS_PATH_PATTERN.test(path)) window.localStorage.setItem(LAST_PROJECTS_PATH_KEY, path);
    else window.localStorage.removeItem(LAST_PROJECTS_PATH_KEY);
  } catch {
    // Remembering the last project view is a convenience, never a requirement.
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
