import { useEffect, useState } from "react";

import { fetchMemoryBackfill } from "@/lib/api";
import type { MemoryBackfillPayload } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

/** Memory update progress: loaded once, then kept in sync with runtime frames. */
export function useMemoryBackfill(): MemoryBackfillPayload {
  const { client, getToken } = useClient();
  const [state, setState] = useState<MemoryBackfillPayload>({ active: false });

  useEffect(() => {
    let cancelled = false;
    void fetchMemoryBackfill(getToken())
      .then((next) => { if (!cancelled) setState(next); })
      .catch(() => {});
    const unsubscribe = client.onEvent((frame) => {
      if (frame.event === "memory_backfill" && frame.memory_backfill) {
        setState(frame.memory_backfill);
      }
    });
    return () => { cancelled = true; unsubscribe(); };
  }, [client, getToken]);

  return state;
}
