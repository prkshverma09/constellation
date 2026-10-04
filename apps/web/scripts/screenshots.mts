import { mkdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { chromium } from "@playwright/test";

const origin = process.env.WEB_BASE ?? "http://localhost:3000";
const output = path.join(os.homedir(), "shots");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });

async function go(url: string, waitUntil: "networkidle" | "domcontentloaded" = "networkidle") {
  await page.goto(`${origin}${url}`, { waitUntil });
}
async function save(name: string, selector: string, viewportOnly = false) {
  const target = page.locator(selector);
  await target.waitFor({ state: "visible" });
  const style = await page.addStyleTag({ content: `
    html { scroll-behavior: auto !important; }
    .section-nav, .skip-link, nextjs-portal { display: none !important; }
    ${selector} { width: 1280px !important; max-width: none !important; margin-left: calc(50% - 50vw) !important; }
  ` });
  if (viewportOnly) {
    await target.evaluate((element) => element.scrollIntoView({ block: "start", behavior: "instant" }));
    await page.screenshot({ path: path.join(output, `${name}.png`), animations: "disabled", scale: "css" });
  } else {
    await target.screenshot({ path: path.join(output, `${name}.png`), animations: "disabled", scale: "css" });
  }
  await style.evaluate((element) => element.remove());
}

await go("/d/MONDO%3A0012812?persona=maria");
await save("overview", "#overview");

const showAll = page.getByRole("button", { name: /Show all \(\d+\)/ });
if (await showAll.isVisible()) await showAll.click();
await save("cluster", "#cluster");

const assetsSelector = page.getByLabel("Compare assets against");
const dnm1Id = await assetsSelector.locator("option").filter({ hasText: /^DNM1/ }).getAttribute("value");
if (!dnm1Id) throw new Error("DNM1 is missing from the real API comparator options.");
await go(`/d/MONDO%3A0012812?persona=maria&vs=${encodeURIComponent(dnm1Id)}`);
await page.locator(".asset-card").filter({ hasText: "NCT06555965" }).locator(".coverage-score").waitFor();
await save("assets", "#assets");

const peopleSelector = page.getByLabel("Compare bridge people against");
if (await peopleSelector.inputValue() !== dnm1Id) {
  throw new Error("The DNM1 deep link did not select the bridge comparator.");
}
await page.getByRole("heading", { name: "Ingo Helbig" }).waitFor();
await save("people", "#people", true);

await go("/d/MONDO%3A0012812/dossier?persona=maria", "domcontentloaded");
await page.locator(".dossier-header").waitFor({ state: "visible" });
const dossierStyle = await page.addStyleTag({ content: ".section-nav, .skip-link, nextjs-portal { display: none !important; }" });
await page.screenshot({ path: path.join(output, "dossier.png"), animations: "disabled", scale: "css" });
await dossierStyle.evaluate((element) => element.remove());

await go("/d/MONDO%3A0014859?persona=maria");
await save("gap", "#coverage");

await browser.close();
console.log([
  "overview.png",
  "cluster.png",
  "assets.png",
  "people.png",
  "dossier.png",
  "gap.png",
].map((file) => path.join(output, file)).join("\n"));
