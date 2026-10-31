import { useCallback, useEffect, useState } from "react";

import {
  cancelCredentialAuth,
  fetchCredentials,
  startCredentialOAuth,
  submitCredentialAuth,
} from "@/lib/api";
import type { CredentialAuthRequest } from "@/lib/types";
import { useClient } from "@/providers/ClientProvider";

function upsert(
  current: CredentialAuthRequest[],
  incoming: CredentialAuthRequest,
): CredentialAuthRequest[] {
  const without = current.filter((item) => item.id !== incoming.id);
  return [...without, incoming];
}

/**
 * Live authentication cards. Requests arrive as `auth_request` frames; on
 * mount we also read the pending list so a reload does not lose a card.
 */
export function useAuthRequests() {
  const { client, getToken } = useClient();
  const [requests, setRequests] = useState<CredentialAuthRequest[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const off = client.onEvent((frame) => {
      if (frame.event !== "auth_request" || !frame.auth_request) return;
      setRequests((current) => upsert(current, frame.auth_request as CredentialAuthRequest));
    });
    void fetchCredentials(getToken())
      .then((state) => {
        if (!cancelled) setRequests(state.pending);
      })
      .catch(() => {
        // A missing endpoint on an older gateway simply means no cards.
      });
    return () => {
      cancelled = true;
      off();
    };
  }, [client, getToken]);

  const submit = useCallback(
    async (requestId: string, values: Record<string, string>) => {
      setBusy(requestId);
      setError(null);
      try {
        await submitCredentialAuth(client, requestId, values);
        setRequests((current) =>
          current.map((item) =>
            item.id === requestId ? { ...item, status: "completed" } : item,
          ),
        );
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setBusy(null);
      }
    },
    [client],
  );

  const cancel = useCallback(
    async (requestId: string) => {
      setBusy(requestId);
      setError(null);
      try {
        await cancelCredentialAuth(client, requestId);
        setRequests((current) =>
          current.map((item) =>
            item.id === requestId ? { ...item, status: "cancelled" } : item,
          ),
        );
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        setBusy(null);
      }
    },
    [client],
  );

  const startOAuth = useCallback(
    async (request: CredentialAuthRequest) => {
      setBusy(request.id);
      setError(null);
      try {
        const { authorization_url: authorizationUrl } = await startCredentialOAuth(
          client,
          request.connector_id,
          request.id,
        );
        window.location.assign(authorizationUrl);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : String(reason));
        setBusy(null);
      }
    },
    [client],
  );

  const pending = requests.filter((request) => request.status === "pending");
  return { pending, busy, error, submit, cancel, startOAuth };
}
