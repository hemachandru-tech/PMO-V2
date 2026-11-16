import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BURST_MS } from "../components/BrandLoader";
import { AssistantAvatar } from "../features/ask/ChatParts";

describe("brand burst when loading finishes", () => {
  afterEach(() => vi.useRealTimers());

  it("bursts once when a reply arrives, then settles to the logo", () => {
    vi.useFakeTimers();
    const { container } = render(<AssistantAvatar burst />);
    expect(container.querySelector("[data-brand-burst]")).not.toBeNull();
    expect(container.querySelectorAll(".brand-spark").length).toBeGreaterThan(8);
    act(() => { vi.advanceTimersByTime(BURST_MS + 10); });
    expect(container.querySelector("[data-brand-burst]")).toBeNull();
    expect(container.querySelector("img")).not.toBeNull();
  });

  it("does not burst for a reply loaded from history or while thinking", () => {
    expect(render(<AssistantAvatar />).container.querySelector("[data-brand-burst]")).toBeNull();
    expect(render(<AssistantAvatar busy burst />).container.querySelector("[data-brand-burst]")).toBeNull();
  });

  it("does not burst when the person asked for less motion", () => {
    const original = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: q.includes("reduce"), media: q, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
    try {
      expect(render(<AssistantAvatar burst />).container.querySelector("[data-brand-burst]")).toBeNull();
    } finally {
      window.matchMedia = original;
    }
  });
});
