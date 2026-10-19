import { defineConfig } from "vitest/config";

// Unit tests for the dashboard's pure logic and API client. happy-dom gives
// them window, localStorage and events without a browser.
export default defineConfig({
  test: {
    environment: "happy-dom",
    include: ["src/**/*.test.ts"],
  },
});
