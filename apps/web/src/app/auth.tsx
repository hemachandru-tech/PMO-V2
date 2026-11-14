import { InMemoryWebStorage, User, UserManager, WebStorageStateStore } from "oidc-client-ts";
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, setTokenSource } from "../api/client";
import { Button } from "../components/ui/button";
import { usePublicBranding } from "./branding";
import { getConfig } from "./config";

/**
 * OIDC Authorization Code + PKCE against Keycloak, which brokers Microsoft Entra ID (FEAT-001, ADR-003).
 * Tokens are kept in memory only; the short-lived PKCE state uses sessionStorage during the redirect.
 */
/** The path being signed in to, remembered across the redirect so its code can load during sign-in. */
export const RETURN_TO_KEY = "tela.returnTo";

export function createUserManager(): UserManager {
  const cfg = getConfig();
  return new UserManager({
    authority: cfg.oidcAuthority,
    client_id: cfg.oidcClientId,
    redirect_uri: `${window.location.origin}/auth/callback`,
    post_logout_redirect_uri: `${window.location.origin}/`,
    response_type: "code",
    scope: "openid profile email",
    userStore: new WebStorageStateStore({ store: new InMemoryWebStorage() }),
    stateStore: new WebStorageStateStore({ store: window.sessionStorage }),
    automaticSilentRenew: true,
  });
}

interface AuthState {
  user: User | null;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children, manager }: { children: ReactNode; manager?: UserManager }) {
  const userManager = useMemo(() => manager ?? createUserManager(), [manager]);
  const [user, setUser] = useState<User | null>(null);
  const [error, setError] = useState<string | null>(null);
  const brand = usePublicBranding();

  useEffect(() => {
    let active = true;
    let currentToken: string | null = null;

    const accept = (u: User | null) => {
      currentToken = u?.access_token ?? null;
      setTokenSource(() => currentToken);
      if (active) setUser(u);
    };
    userManager.events.addUserLoaded(accept);
    userManager.events.addUserUnloaded(() => accept(null));

    void (async () => {
      try {
        if (window.location.pathname === "/auth/callback") {
          const signedIn = await userManager.signinRedirectCallback();
          const target = (signedIn.state as { returnTo?: string } | undefined)?.returnTo ?? "/";
          window.history.replaceState({}, "", target);
          accept(signedIn);
          return;
        }
        const existing = await userManager.getUser();
        if (existing && !existing.expired) {
          accept(existing);
        } else {
          const returnTo = window.location.pathname + window.location.search;
          try {
            window.sessionStorage.setItem(RETURN_TO_KEY, window.location.pathname);
          } catch {
            // Only a loading hint.
          }
          await userManager.signinRedirect({ state: { returnTo } });
        }
      } catch {
        if (active) setError("Sign-in failed. Please try again.");
      }
    })();

    return () => {
      active = false;
      userManager.events.removeUserLoaded(accept);
    };
  }, [userManager]);

  const value = useMemo<AuthState>(
    () => ({
      user,
      signOut: async () => {
        // Audit the logout first (API-002), then end the broker session. signoutRedirect sends the
        // id_token_hint Keycloak needs to end its SSO session, and clears the in-memory user itself.
        try {
          await api("/api/v1/auth/logout", { method: "POST" });
        } catch {
          // The broker logout below still ends the session.
        }
        await userManager.signoutRedirect({ id_token_hint: user?.id_token });
      },
    }),
    [user, userManager],
  );

  if (error) {
    return (
      <main className="tl mx-auto mt-[15vh] grid max-w-xl justify-items-center gap-3 px-4 text-center">
        <h1 className="text-title">{brand.app_name}</h1>
        <p role="alert">{error}</p>
        <Button onClick={() => window.location.assign("/")}>Try again</Button>
      </main>
    );
  }
  if (!user) {
    return (
      <main className="tl mx-auto mt-[15vh] max-w-xl px-4 text-center text-ink-muted" aria-busy="true">
        <p>Signing you in with Microsoft…</p>
      </main>
    );
  }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

/** Test helper: provide an auth context without OIDC. */
export function TestAuthProvider({ children, token = "test-token" }: { children: ReactNode; token?: string }) {
  setTokenSource(() => token);
  const value: AuthState = { user: { access_token: token } as User, signOut: async () => undefined };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
