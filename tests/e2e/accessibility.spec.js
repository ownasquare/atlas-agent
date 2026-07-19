const { test, expect } = require("@playwright/test");
const {
  captureBrowserIssues,
  expectControlBoundaryContrast,
  expectMinimumFunctionalText,
  expectMinimumTargets,
  expectNoA11yViolations,
  expectNoHorizontalOverflow,
} = require("./helpers");

async function openWorkspace(page) {
  await page.goto("/");
  await expect(page).toHaveTitle("Atlas · Workspace");
  await expect(page.getByRole("heading", { name: "What would you like to accomplish?" })).toBeVisible();
}

test("setup-needed state is clear, bounded, and accessible", async ({ page }, testInfo) => {
  const issues = captureBrowserIssues(page);
  await page.route("**/api/health", async (route) => {
    await route.fulfill({
      json: {
        version: "0.3.1",
        model: "openai:gpt-4.1-mini",
        model_configured: false,
        memory_enabled: true,
        code_backend: "disabled",
      },
    });
  });

  await openWorkspace(page);
  await expect(page.getByText("Connect a model to start tasks")).toBeVisible();
  await expect(page.getByRole("button", { name: "Start task" })).toBeDisabled();
  await expectNoA11yViolations(page, testInfo, "setup-needed");
  await expectNoHorizontalOverflow(page);
  await expectMinimumFunctionalText(page);
  await expectMinimumTargets(page);
  await expectControlBoundaryContrast(page);
  expect(issues).toEqual([]);
});

test("working and completed states expose progress, evidence, and files", async ({ page }, testInfo) => {
  const issues = captureBrowserIssues(page);
  await openWorkspace(page);
  await page.getByLabel("Describe your task").fill("Show the working state, then complete the deterministic brief.");
  await page.getByRole("button", { name: "Start task" }).click();

  await expect(page.locator("#mainContent")).toHaveAttribute("aria-busy", "true");
  await expect(page.locator("#runStatus")).toHaveText(/Planning|Working/);
  await expect(page.getByLabel("Describe your task")).toBeFocused();
  await expectNoA11yViolations(page, testInfo, "working");

  await expect(page.locator("#runStatus")).toHaveText("Complete");
  await expect(page.getByRole("heading", { name: "Fixture result" })).toBeVisible();
  await expect(page.locator("#answerPanel")).toBeFocused();
  await expect(page.getByRole("link", { name: /LangGraph overview/ })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "reports/deterministic-local-brief.md", exact: true }),
  ).toBeVisible();
  await expectNoA11yViolations(page, testInfo, "completed");
  await expectNoHorizontalOverflow(page);
  await expectMinimumFunctionalText(page);
  await expectMinimumTargets(page);
  expect(issues).toEqual([]);
});

test("approval keeps focus contained and restores the task action", async ({ page }, testInfo) => {
  const issues = captureBrowserIssues(page);
  await openWorkspace(page);
  await page.getByLabel("Describe your task").fill("Request approval for the fixture action.");
  await page.getByRole("button", { name: "Start task" }).click();

  const dialog = page.getByRole("dialog", { name: "Approve creating the deterministic local fixture artifact?" });
  await expect(dialog).toBeVisible();
  const reject = page.getByRole("button", { name: "Don’t allow" });
  await expect(reject).toBeFocused();
  await expectNoA11yViolations(page, testInfo, "approval");
  await page.keyboard.press("Shift+Tab");
  expect(await page.evaluate(() => document.querySelector("#approvalPanel").contains(document.activeElement))).toBe(true);
  await page.getByRole("button", { name: "Allow once" }).focus();
  await page.keyboard.press("Tab");
  expect(await page.evaluate(() => document.querySelector("#approvalPanel").contains(document.activeElement))).toBe(true);
  await reject.focus();
  await page.keyboard.press("Enter");

  await expect(dialog).toBeHidden();
  await expect(page.getByText(/approval was not granted/i)).toBeVisible();
  await expect(page.getByLabel("Describe your task")).toBeFocused();
  await expectNoA11yViolations(page, testInfo, "approval-rejected");
  expect(issues).toEqual([]);
});

test("truncated progress becomes a persistent recoverable error", async ({ page }, testInfo) => {
  const issues = captureBrowserIssues(page);
  await openWorkspace(page);
  await page.getByLabel("Describe your task").fill("Return a truncated stream for deterministic error proof.");
  await page.getByRole("button", { name: "Start task" }).click();

  await expect(page.getByRole("alert")).toContainText("before returning a result");
  await expect(page.locator("#runStatus")).toHaveText("Could not finish");
  await expect(page.getByRole("button", { name: "Start task" })).toBeEnabled();
  await expect(page.getByLabel("Describe your task")).toBeFocused();
  await expectNoA11yViolations(page, testInfo, "persistent-error");
  expect(issues).toEqual([]);
});

for (const width of [320, 375, 768, 1440]) {
  for (const theme of ["light", "dark"]) {
    test(`${width}px ${theme} workspace reflows without inaccessible controls`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: width <= 375 ? 800 : 1000 });
      await page.addInitScript((selectedTheme) => {
        window.localStorage.setItem("atlas-theme-v2", selectedTheme);
      }, theme);
      await openWorkspace(page);
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      await expect(page.getByLabel("Describe your task")).toBeVisible();
      await expectNoHorizontalOverflow(page);
      await expectMinimumFunctionalText(page);
      await expectMinimumTargets(page);
      await expectControlBoundaryContrast(page);
      await expectNoA11yViolations(page, testInfo, `${width}-${theme}`);
    });
  }
}

test("skip link, text resize, reduced motion, and forced colors remain usable", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce", forcedColors: "active" });
  await openWorkspace(page);
  await page.keyboard.press("Tab");
  const skipLink = page.getByRole("link", { name: "Skip to workspace" });
  await expect(skipLink).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.locator("#mainContent")).toBeFocused();
  const outline = await page.locator("#mainContent").evaluate((element) => window.getComputedStyle(element).outlineStyle);
  expect(outline).not.toBe("none");
  expect(await page.evaluate(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
  expect(await page.evaluate(() => window.matchMedia("(forced-colors: active)").matches)).toBe(true);

  await page.evaluate(() => {
    document.body.style.fontSize = "30px";
  });
  await expectNoHorizontalOverflow(page);
  await expectNoA11yViolations(page, testInfo, "forced-colors-text-resize");
});
