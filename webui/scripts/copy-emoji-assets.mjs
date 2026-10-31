/**
 * Prepare the bundled emoji artwork.
 *
 * Twemoji SVGs are copied into webui/public/emoji under the catalogue's own
 * hexcodes, so one code path can address any emoji without guessing file names
 * or hitting a missing glyph. The manifest lists what the artwork covers; text
 * emoji the set does not know keep the platform glyph instead of a broken image.
 */
import { copyFile, mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const webui = path.resolve(here, "..");
const datasetPath = path.join(webui, "node_modules/emojibase-data/en/data.json");
const sourceDir = path.join(webui, "node_modules/@twemoji/svg");
const targetDir = path.join(webui, "public/emoji");
const manifestPath = path.join(targetDir, "manifest.json");

const dataset = JSON.parse(await readFile(datasetPath, "utf8"));

/** Source files this hexcode may have shipped under. */
function candidates(hexcode) {
  const lower = hexcode.toLowerCase();
  return [...new Set([`${lower}.svg`, `${lower.replaceAll("-fe0f", "")}.svg`])];
}

await mkdir(targetDir, { recursive: true });
const existing = new Set(await readdir(targetDir));
const covered = [];
let copied = 0;

for (const entry of dataset) {
  if (!entry.hexcode) continue;
  const source = candidates(entry.hexcode).find((name) => existsSync(path.join(sourceDir, name)));
  if (!source) continue;
  const canonical = `${entry.hexcode.toLowerCase()}.svg`;
  covered.push(entry.hexcode.toLowerCase());
  if (existing.has(canonical)) continue;
  await copyFile(path.join(sourceDir, source), path.join(targetDir, canonical));
  copied += 1;
}

await writeFile(manifestPath, `${JSON.stringify(covered.sort())}\n`, "utf8");
console.log(`emoji artwork: ${covered.length} covered, ${copied} copied`);
