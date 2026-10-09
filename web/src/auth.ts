import { UserManager, WebStorageStateStore } from "oidc-client-ts";
import type { AppConfig } from "./config";

/**
 * Cognito managed login with the authorization code flow + PKCE (no client
 * secret). Tokens are kept in localStorage so the phone app stays signed in;
 * the strict Content-Security-Policy on CloudFront (scripts from this origin
 * only) is what protects them from injected scripts.
 */
export function createUserManager(config: AppConfig): UserManager {
  const home = `${window.location.origin}/`; // must match a Cognito callback URL exactly
  return new UserManager({
    authority: config.issuer,
    client_id: config.clientId,
    redirect_uri: home,
    post_logout_redirect_uri: home,
    response_type: "code",
    scope: "openid email",
    userStore: new WebStorageStateStore({ store: window.localStorage }),
    automaticSilentRenew: true, // uses the refresh token, no iframe
    // Cognito's discovery document doesn't list these.
    metadataSeed: {
      revocation_endpoint: `${config.loginDomain}/oauth2/revoke`,
    },
  });
}

export function isSigninCallback(): boolean {
  const params = new URLSearchParams(window.location.search);
  return params.has("code") && params.has("state");
}

let callback: Promise<unknown> | null = null;

/** Exchange ?code for tokens once, even if React runs the effect twice (StrictMode). */
export function completeSignin(manager: UserManager): Promise<unknown> {
  callback ??= manager.signinRedirectCallback().finally(() => window.history.replaceState({}, "", "/"));
  return callback;
}

/** Revoke the refresh token, forget the local session, then end Cognito's session cookie. */
export async function signOut(manager: UserManager, config: AppConfig): Promise<void> {
  try {
    await manager.revokeTokens(["refresh_token"]);
  } catch {
    // Signing out locally still matters if revocation fails (e.g. offline).
  }
  await manager.removeUser();
  const logout = new URL(`${config.loginDomain}/logout`);
  logout.searchParams.set("client_id", config.clientId);
  logout.searchParams.set("logout_uri", `${window.location.origin}/`);
  window.location.assign(logout.toString());
}
