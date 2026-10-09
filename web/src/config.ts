/** Runtime settings, served as /config.json (written by Terraform; see README). */
export interface AppConfig {
  apiUrl: string; // ends with "/"
  issuer: string; // https://cognito-idp.<region>.amazonaws.com/<pool-id>
  clientId: string;
  loginDomain: string; // https://<prefix>.auth.<region>.amazoncognito.com
}

export async function loadConfig(): Promise<AppConfig> {
  const resp = await fetch("/config.json", { cache: "no-store" });
  if (!resp.ok) throw new Error(`config.json: HTTP ${resp.status}`);
  const config = (await resp.json()) as AppConfig;
  for (const key of ["apiUrl", "issuer", "clientId", "loginDomain"] as const) {
    if (!config[key]) throw new Error(`config.json is missing ${key}`);
  }
  return { ...config, apiUrl: config.apiUrl.endsWith("/") ? config.apiUrl : `${config.apiUrl}/` };
}
