import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import type { User } from "oidc-client-ts";
import { ApiClient } from "./api";
import { completeSignin, createUserManager, isSigninCallback, signOut } from "./auth";
import type { AppConfig } from "./config";
import { ReceiptDetail } from "./pages/ReceiptDetail";
import { ReceiptList } from "./pages/ReceiptList";
import { Upload } from "./pages/Upload";

const ApiContext = createContext<ApiClient | null>(null);

export function useApi(): ApiClient {
  const api = useContext(ApiContext);
  if (!api) throw new Error("useApi outside the signed-in app");
  return api;
}

export function App({ config }: { config: AppConfig }) {
  const manager = useMemo(() => createUserManager(config), [config]);
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (isSigninCallback()) await completeSignin(manager);
        const current = await manager.getUser();
        if (current && !current.expired) {
          if (!cancelled) setUser(current);
          return;
        }
        // Expired access token but a refresh token: renew without a redirect.
        if (current?.refresh_token) {
          const renewed = await manager.signinSilent().catch(() => null);
          if (renewed) {
            if (!cancelled) setUser(renewed);
            return;
          }
        }
        await manager.signinRedirect();
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      }
    })();
    const onLoaded = (u: User) => setUser(u);
    manager.events.addUserLoaded(onLoaded);
    return () => {
      cancelled = true;
      manager.events.removeUserLoaded(onLoaded);
    };
  }, [manager]);

  const api = useMemo(
    () =>
      new ApiClient(config.apiUrl, async () => {
        const current = await manager.getUser();
        if (current && !current.expired) return current.access_token;
        const renewed = await manager.signinSilent().catch(() => null);
        if (renewed) return renewed.access_token;
        await manager.signinRedirect();
        throw new Error("Signing in again…");
      }),
    [config, manager],
  );

  if (error) {
    return (
      <main className="page">
        <p className="error">Sign-in failed: {error}</p>
        <button onClick={() => manager.signinRedirect()}>Try again</button>
      </main>
    );
  }
  if (!user) return <main className="page muted">Signing in…</main>;

  return (
    <ApiContext.Provider value={api}>
      <header className="topbar">
        <Link to="/" className="brand">
          Receipts
        </Link>
        <button className="link" onClick={() => signOut(manager, config)}>
          Sign out
        </button>
      </header>
      <Routes>
        <Route path="/" element={<ReceiptList />} />
        <Route path="/upload" element={<Upload />} />
        <Route path="/receipts/:id" element={<ReceiptDetail />} />
        <Route path="*" element={<main className="page">Page not found. <Link to="/">Back to receipts</Link></main>} />
      </Routes>
    </ApiContext.Provider>
  );
}
