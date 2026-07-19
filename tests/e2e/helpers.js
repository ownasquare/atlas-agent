const { expect } = require("@playwright/test");
const AxeBuilder = require("@axe-core/playwright").default;

const WCAG_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

function captureBrowserIssues(page) {
  const issues = [];
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) {
      issues.push(`${message.type()}: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => issues.push(`pageerror: ${error.message}`));
  return issues;
}

function colorChannels(value) {
  const channels = String(value).match(/[\d.]+/g);
  return channels ? channels.slice(0, 3).map(Number) : [];
}

function relativeLuminance(value) {
  return colorChannels(value)
    .map((channel) => channel / 255)
    .map((channel) => (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4))
    .reduce((total, channel, index) => total + channel * [0.2126, 0.7152, 0.0722][index], 0);
}

function contrastRatio(first, second) {
  const lighter = Math.max(relativeLuminance(first), relativeLuminance(second));
  const darker = Math.min(relativeLuminance(first), relativeLuminance(second));
  return (lighter + 0.05) / (darker + 0.05);
}

async function expectControlBoundaryContrast(page) {
  const samples = await page
    .locator("textarea, .capability-disclosure > summary, .example-row button")
    .evaluateAll((items) =>
      items
        .filter((item) => item.getClientRects().length)
        .map((item) => {
          const style = window.getComputedStyle(item);
          return {
            label: (item.getAttribute("aria-label") || item.textContent || item.id).trim().slice(0, 80),
            border: style.borderTopColor,
            background: style.backgroundColor,
          };
        }),
    );
  const failures = samples
    .map((sample) => ({ ...sample, ratio: contrastRatio(sample.border, sample.background) }))
    .filter((sample) => sample.ratio < 3);
  expect(failures, "control boundaries below 3:1 contrast").toEqual([]);
}

async function expectNoA11yViolations(page, testInfo, label) {
  const result = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
  await testInfo.attach(`axe-${label}`, {
    body: JSON.stringify(result, null, 2),
    contentType: "application/json",
  });
  const summary = result.violations.map((violation) => ({
    id: violation.id,
    impact: violation.impact,
    targets: violation.nodes.map((item) => item.target),
  }));
  expect(summary, `${label} accessibility violations`).toEqual([]);
}

async function expectNoHorizontalOverflow(page) {
  const metrics = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(metrics.scrollWidth - metrics.clientWidth, JSON.stringify(metrics)).toBeLessThanOrEqual(1);
}

async function expectMinimumFunctionalText(page) {
  const undersized = await page.locator("button, input, textarea, summary, a").evaluateAll((items) =>
    items
      .filter((item) => {
        const style = window.getComputedStyle(item);
        return style.display !== "none" && style.visibility !== "hidden" && item.getClientRects().length;
      })
      .map((item) => ({
        element: item.tagName.toLowerCase(),
        label: (item.getAttribute("aria-label") || item.textContent || item.id).trim().slice(0, 80),
        pixels: Number.parseFloat(window.getComputedStyle(item).fontSize),
      }))
      .filter((item) => item.pixels < 14),
  );
  expect(undersized, "visible functional text below 14px").toEqual([]);
}

async function expectMinimumTargets(page) {
  const undersized = await page
    .locator("button, input, textarea, summary, a.brand")
    .evaluateAll((items) =>
      items
        .filter((item) => {
          const style = window.getComputedStyle(item);
          return style.display !== "none" && style.visibility !== "hidden" && item.getClientRects().length;
        })
        .map((item) => {
          const rect = item.getBoundingClientRect();
          return {
            element: item.tagName.toLowerCase(),
            label: (item.getAttribute("aria-label") || item.textContent || item.id).trim().slice(0, 80),
            width: Math.round(rect.width * 10) / 10,
            height: Math.round(rect.height * 10) / 10,
          };
        })
        .filter((item) => item.width < 44 || item.height < 44),
    );
  expect(undersized, "visible primary targets below 44px").toEqual([]);
}

module.exports = {
  captureBrowserIssues,
  expectControlBoundaryContrast,
  expectMinimumFunctionalText,
  expectMinimumTargets,
  expectNoA11yViolations,
  expectNoHorizontalOverflow,
};
