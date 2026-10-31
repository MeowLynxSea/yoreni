export function linearManifestUrl(
  publicBaseUrl: string,
  webhookPath: string,
  callbackPath: string,
): string {
  const manifest = {
    $schema: "https://linear.app/.well-known/oauth-app-manifest.schema.json",
    schemaVersion: "1.0.0",
    distribution: "private",
    display: {
      description: "Mention or delegate issues to Yoreni as a native Linear agent.",
      iconUrl: "https://raw.githubusercontent.com/MeowLynxSea/yoreni/main/images/yoreni_logo.png",
    },
    developer: { name: "Yoreni" },
    oauth: {
      client_name: "Yoreni Agent",
      client_uri: "https://github.com/MeowLynxSea/yoreni",
      redirect_uris: [`${publicBaseUrl}${callbackPath}`],
      grant_types: ["authorization_code"],
    },
    webhook: {
      enabled: true,
      url: `${publicBaseUrl}${webhookPath}`,
      resourceTypes: [
        "AgentSessionEvent",
        "PermissionChange",
        "OAuthAuthorization",
      ],
    },
  };
  return `https://linear.app/settings/api/applications/new?${new URLSearchParams({
    manifest: JSON.stringify(manifest),
  })}`;
}
