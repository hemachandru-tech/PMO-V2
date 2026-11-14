import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { AppRoutes, preloadRoute, Providers } from "./app/App";
import { AuthProvider, RETURN_TO_KEY } from "./app/auth";
import { loadPublicBranding } from "./app/branding";
import { loadConfig } from "./app/config";
import "./index.css";

function pathBeingOpened() {
  if (window.location.pathname !== "/auth/callback") return window.location.pathname;
  try {
    return window.sessionStorage.getItem(RETURN_TO_KEY);
  } catch {
    return null;
  }
}

const root = createRoot(document.getElementById("root")!);

if (window.location.pathname.startsWith("/print/")) {
  // The PDF renderer's print route (US-096) renders an injected payload: no configuration, sign-in or app shell.
  void import("./features/print/PrintApp").then(({ PrintApp }) => root.render(<StrictMode><PrintApp /></StrictMode>));
} else {
  startApp();
}

function startApp() {
  preloadRoute(pathBeingOpened());

  loadConfig()
    // The tenant's colours, tab title and favicon are applied before the first paint (ENH-006, US-151), so nobody sees
    // the Tela colours flash first; a slow answer never holds the app back for long.
    .then(() => Promise.race([loadPublicBranding(), new Promise((done) => setTimeout(done, 1500))]))
    .then(() => {
      root.render(
        <StrictMode>
          <AuthProvider>
            <Providers>
              <AppRoutes />
            </Providers>
          </AuthProvider>
        </StrictMode>,
      );
    })
    .catch((err: Error) => root.render(<p role="alert">{err.message}</p>));
}
