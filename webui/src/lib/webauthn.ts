import type {
  PasskeyAssertionOptions,
  PasskeyAssertionPayload,
  PasskeyRegistrationOptions,
  PasskeyRegistrationPayload,
} from "./types";

/**
 * Browser glue for platform authenticators. The gateway verifies every
 * ceremony; this module only translates between the wire format and the
 * ArrayBuffer-based Credential Management API.
 */

export type PasskeyFailure = "already-registered" | "unsupported";

export class PasskeyError extends Error {
  readonly code: PasskeyFailure;

  constructor(code: PasskeyFailure) {
    super(code);
    this.name = "PasskeyError";
    this.code = code;
  }
}

export function passkeysSupported(): boolean {
  return (
    typeof window !== "undefined"
    && typeof window.PublicKeyCredential !== "undefined"
    && typeof navigator !== "undefined"
    && typeof navigator.credentials !== "undefined"
  );
}

/** True when the page is served over plain HTTP on a non-loopback host. */
export function passkeysNeedSecureContext(): boolean {
  return typeof window !== "undefined" && window.isSecureContext === false;
}

function fromB64url(value: string): Uint8Array<ArrayBuffer> {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index);
  }
  return bytes;
}

function toB64url(value: ArrayBuffer): string {
  const bytes = new Uint8Array(value);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function isCancellation(reason: unknown): boolean {
  return (
    typeof DOMException !== "undefined"
    && reason instanceof DOMException
    && (reason.name === "NotAllowedError" || reason.name === "AbortError")
  );
}

function translate(reason: unknown): never {
  if (reason instanceof DOMException && reason.name === "InvalidStateError") {
    throw new PasskeyError("already-registered");
  }
  if (reason instanceof DOMException && reason.name === "NotSupportedError") {
    throw new PasskeyError("unsupported");
  }
  throw reason;
}

/** Register this device as an authenticator; null means the user cancelled. */
export async function createPasskey(
  options: PasskeyRegistrationOptions,
  name: string,
): Promise<PasskeyRegistrationPayload | null> {
  let credential: Credential | null;
  try {
    credential = await navigator.credentials.create({
      publicKey: {
        challenge: fromB64url(options.challenge),
        rp: options.rp,
        user: {
          id: fromB64url(options.user.id),
          name: options.user.name,
          displayName: options.user.displayName,
        },
        pubKeyCredParams: options.pubKeyCredParams,
        authenticatorSelection: options.authenticatorSelection,
        attestation: options.attestation,
        timeout: options.timeout,
        excludeCredentials: options.excludeCredentials.map((entry) => ({
          type: entry.type,
          id: fromB64url(entry.id),
          ...(entry.transports ? { transports: entry.transports as AuthenticatorTransport[] } : {}),
        })),
      },
    });
  } catch (reason) {
    if (isCancellation(reason)) return null;
    translate(reason);
  }
  if (!(credential instanceof PublicKeyCredential)) return null;
  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    client_data_json: toB64url(response.clientDataJSON),
    attestation_object: toB64url(response.attestationObject),
    name,
    transports: response.getTransports?.() ?? [],
  };
}

/** Produce one assertion for the gateway; null means the user cancelled. */
export async function assertPasskey(
  options: PasskeyAssertionOptions,
): Promise<PasskeyAssertionPayload | null> {
  let credential: Credential | null;
  try {
    credential = await navigator.credentials.get({
      publicKey: {
        challenge: fromB64url(options.challenge),
        rpId: options.rpId,
        allowCredentials: options.allowCredentials.map((entry) => ({
          type: entry.type,
          id: fromB64url(entry.id),
          ...(entry.transports ? { transports: entry.transports as AuthenticatorTransport[] } : {}),
        })),
        userVerification: options.userVerification,
        timeout: options.timeout,
      },
    });
  } catch (reason) {
    if (isCancellation(reason)) return null;
    translate(reason);
  }
  if (!(credential instanceof PublicKeyCredential)) return null;
  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    client_data_json: toB64url(response.clientDataJSON),
    authenticator_data: toB64url(response.authenticatorData),
    signature: toB64url(response.signature),
    credential_id: toB64url(credential.rawId),
  };
}
