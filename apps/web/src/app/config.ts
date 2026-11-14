/** Runtime configuration, loaded from /config.json so one image serves every environment. */
export interface AppConfig {
  oidcAuthority: string;
  oidcClientId: string;
  apiBaseUrl: string;
}

let current: AppConfig | null = null;

export async function loadConfig(fetchImpl: typeof fetch = fetch): Promise<AppConfig> {
  const resp = await fetchImpl("/config.json", { cache: "no-store" });
  if (!resp.ok) throw new Error("Could not load application configuration.");
  const data = (await resp.json()) as Partial<AppConfig>;
  // apiBaseUrl may be "" when the API is served from the same origin behind the edge proxy.
  if (!data.oidcAuthority || !data.oidcClientId || typeof data.apiBaseUrl !== "string") {
    throw new Error("Application configuration is incomplete.");
  }
  current = { oidcAuthority: data.oidcAuthority, oidcClientId: data.oidcClientId, apiBaseUrl: data.apiBaseUrl };
  return current;
}

export function getConfig(): AppConfig {
  if (!current) throw new Error("Configuration not loaded.");
  return current;
}

export function setConfigForTests(cfg: AppConfig): void {
  current = cfg;
}
