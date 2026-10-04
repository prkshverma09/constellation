import { mkdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { chromium } from "@playwright/test";

const origin = process.env.WEB_BASE ?? "http://localhost:3000";
const output = path.join(os.homedir(), "shots");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 1400 }, deviceScaleFactor: 1 });

async function go(url: string, waitUntil: "networkidle" | "domcontentloaded" = "networkidle") {
  await page.goto(`${origin}${url}`, { waitUntil });
}
async function save(name: string, selector: string) {
  const target = page.locator(selector);
  await target.waitFor({ state: "visible" });
  const style = await page.addStyleTag({ content: `
    html { scroll-behavior: auto !important; }
    .section-nav, .skip-link, nextjs-portal { display: none !important; }
  ` });
  await target.evaluate((element) => element.scrollIntoView({ block: "start", behavior: "instant" }));
  await page.screenshot({ path: path.join(output, `${name}.png`), animations: "disabled", scale: "css" });
  await style.evaluate((element) => element.remove());
}

async function waitForHealth() {
  await page.locator(".health-badge").waitFor({ state: "visible" });
  await page.waitForFunction(
    () => !document.querySelector(".health-badge")?.textContent?.includes("checking"),
  );
}

await go("/d/MONDO%3A0012812?persona=maria");
await save("overview", "#overview");

const showAll = page.getByRole("button", { name: /Show all \(\d+\)/ });
if (await showAll.isVisible()) await showAll.click();
await page.setViewportSize({ width: 1280, height: 2400 });
await save("cluster", "#cluster");

const assetsSelector = page.getByLabel("Compare assets against");
const dnm1Id = await assetsSelector.locator("option").filter({ hasText: /^DNM1/ }).getAttribute("value");
if (!dnm1Id) throw new Error("DNM1 is missing from the real API comparator options.");
await page.setViewportSize({ width: 1280, height: 1400 });
await go(`/d/MONDO%3A0012812?persona=maria&vs=${encodeURIComponent(dnm1Id)}`);
await page.locator(".asset-card").filter({ hasText: "NCT06555965" }).locator(".coverage-score").waitFor();
await save("assets", "#assets");

const peopleSelector = page.getByLabel("Compare bridge people against");
if (await peopleSelector.inputValue() !== dnm1Id) {
  throw new Error("The DNM1 deep link did not select the bridge comparator.");
}
await page.getByRole("heading", { name: "Ingo Helbig" }).waitFor();
await save("people", "#people");

for (const persona of ["maria", "osei"]) {
  await go(`/d/MONDO%3A0012812/dossier?persona=${persona}`, "domcontentloaded");
  await page.locator(".dossier-header").waitFor({ state: "visible" });
  await waitForHealth();
  await page.screenshot({
    path: path.join(output, `dossier-${persona}.png`),
    animations: "disabled",
    scale: "css",
  });
}

await go("/d/MONDO%3A0014859?persona=maria");
await save("gap", "#coverage");

await page.setViewportSize({ width: 375, height: 2400 });
await go("/d/MONDO%3A0012812?persona=maria");
const mobileShowAll = page.getByRole("button", { name: /Show all \(\d+\)/ });
if (await mobileShowAll.isVisible()) await mobileShowAll.click();
await save("cluster-375", "#cluster");

await browser.close();
console.log([
  "overview.png",
  "cluster.png",
  "assets.png",
  "people.png",
  "dossier-maria.png",
  "dossier-osei.png",
  "gap.png",
  "cluster-375.png",
].map((file) => path.join(output, file)).join("\n"));
