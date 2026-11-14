import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterEach } from "vitest";

import { resetBrandingForTests } from "../app/branding";

afterEach(() => {
  cleanup();
  resetBrandingForTests(); // the app name is kept per page load (ENH-006)
});

// Lazy routes load on first visit; under a parallel run that can take longer than the 1 s default wait.
configure({ asyncUtilTimeout: 3000 });

// jsdom lacks these browser APIs, which Radix popovers and menus use (US-134).
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;
Element.prototype.scrollIntoView ??= () => undefined;
Element.prototype.hasPointerCapture ??= () => false;
Element.prototype.releasePointerCapture ??= () => undefined;
