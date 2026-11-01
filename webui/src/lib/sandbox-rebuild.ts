/** Tells the rebuild overlay that this session asked for a rebuild. */

let requested = false;
const listeners = new Set<(requested: boolean) => void>();

export function markSandboxRebuild(value: boolean): void {
  requested = value;
  listeners.forEach((listener) => listener(requested));
}

export function onSandboxRebuild(listener: (requested: boolean) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
