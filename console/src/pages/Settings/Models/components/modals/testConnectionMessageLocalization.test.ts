import { describe, expect, it, vi } from "vitest";
import type { TFunction } from "i18next";
import en from "../../../../../locales/en.json";
import zh from "../../../../../locales/zh.json";
import { getLocalizedTestConnectionMessage } from "./testConnectionMessage";

describe("getLocalizedTestConnectionMessage", () => {
  it("localizes provider-only success and does not use the backend message", () => {
    const t = vi.fn(() => en.models.testProviderInitializationSuccess);

    expect(
      getLocalizedTestConnectionMessage(
        {
          success: true,
          verification: "provider_only",
          message: "Session initialized; chat not tested.",
        },
        t as unknown as TFunction,
      ),
    ).toBe("Provider session initialized; chat was not tested");
    expect(t).toHaveBeenCalledWith("models.testProviderInitializationSuccess");
  });

  it("keeps live success generic with actual English and Chinese translations", () => {
    const enT = vi.fn(() => en.models.testConnectionSuccess);
    const zhT = vi.fn(() => zh.models.testConnectionSuccess);
    const result = {
      success: true,
      verification: "live" as const,
      message: "backend success",
    };

    expect(
      getLocalizedTestConnectionMessage(result, enT as unknown as TFunction),
    ).toBe(en.models.testConnectionSuccess);
    expect(
      getLocalizedTestConnectionMessage(result, zhT as unknown as TFunction),
    ).toBe(zh.models.testConnectionSuccess);
  });
});
