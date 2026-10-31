import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  type ReactNode,
} from "react";

import type { YoreniClient } from "@/lib/yoreni-client";
import type { WebUIIngressLimits } from "@/lib/types";

interface ClientContextValue {
  client: YoreniClient;
  token: string;
  getToken: () => string;
  modelName: string | null;
  ingressLimits: WebUIIngressLimits | null;
  webuiCapabilities: string[];
  /** Keep the saved bootstrap secret in step when the login password changes. */
  onAuthSecretChange?: (value: string) => void;
}

const ClientContext = createContext<ClientContextValue | null>(null);

export function ClientProvider({
  client,
  token,
  modelName = null,
  ingressLimits = null,
  webuiCapabilities = [],
  onAuthSecretChange,
  children,
}: {
  client: YoreniClient;
  token: string;
  modelName?: string | null;
  ingressLimits?: WebUIIngressLimits | null;
  webuiCapabilities?: string[];
  onAuthSecretChange?: (value: string) => void;
  children: ReactNode;
}) {
  const tokenRef = useRef(token);
  tokenRef.current = token;
  const getToken = useCallback(() => tokenRef.current, []);
  const value = useMemo(
    () => ({
      client,
      token,
      getToken,
      modelName,
      ingressLimits,
      webuiCapabilities,
      onAuthSecretChange,
    }),
    [client, getToken, ingressLimits, modelName, onAuthSecretChange, token, webuiCapabilities],
  );

  return <ClientContext.Provider value={value}>{children}</ClientContext.Provider>;
}

export function useClient(): ClientContextValue {
  const ctx = useContext(ClientContext);
  if (!ctx) {
    throw new Error("useClient must be used within a ClientProvider");
  }
  return ctx;
}
