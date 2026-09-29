// Screenshot one local HTML file at phone width through the Chrome
// DevTools protocol, and report every non-file request the page made.
//
//   node shot.mjs <chrome> <file.html> <out.png> <mode> [width]
//
// mode: "on" (scripts run), "off" (script execution disabled),
// "zoom" (scripts run, text zoomed to 200% via the page's root font size).
// Prints one JSON line: {file, mode, width, scrollWidth, requests: [...]}.
// No dependencies beyond Node >= 22 (global WebSocket) and a local
// Chromium-family browser; nothing is fetched.
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [chrome, file, out, mode = "on", widthArg = "390"] = process.argv.slice(2);
if (!chrome || !file || !out) {
  console.error("usage: node shot.mjs <chrome> <file.html> <out.png> <on|off|zoom> [width]");
  process.exit(2);
}
const width = Number(widthArg);
const profile = mkdtempSync(join(tmpdir(), "jimemo-phone-"));
const proc = spawn(chrome, [
  "--headless=new", "--disable-gpu", "--no-first-run",
  "--disable-background-networking", "--disable-component-update",
  `--user-data-dir=${profile}`, "--remote-debugging-port=0", "about:blank",
], { stdio: "ignore" });

const sleep = ms => new Promise(r => setTimeout(r, ms));
function cleanup() {
  try { proc.kill("SIGKILL"); } catch {}
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
}
const deadline = setTimeout(() => { console.error("timeout"); cleanup(); process.exit(1); }, 60000);

let port;
for (let i = 0; i < 200 && !port; i++) {
  const f = join(profile, "DevToolsActivePort");
  if (existsSync(f)) port = readFileSync(f, "utf8").split("\n")[0];
  else await sleep(50);
}
const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const ws = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r, { once: true }));
let seq = 0;
const pending = new Map();
const requests = [];
ws.addEventListener("message", ev => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
  if (msg.method === "Network.requestWillBeSent") {
    const url = msg.params.request.url;
    if (!url.startsWith("file:") && !url.startsWith("data:") && url !== "about:blank") requests.push(url);
  }
});
const send = (method, params = {}) => new Promise((res, rej) => {
  const id = ++seq;
  pending.set(id, m => (m.error ? rej(new Error(`${method}: ${m.error.message}`)) : res(m.result)));
  ws.send(JSON.stringify({ id, method, params }));
});

await send("Network.enable");
await send("Page.enable");
await send("Emulation.setDeviceMetricsOverride",
  { width, height: 844, deviceScaleFactor: 2, mobile: true });
if (mode === "off") await send("Emulation.setScriptExecutionDisabled", { value: true });
const loaded = new Promise(r => ws.addEventListener("message", ev => {
  if (JSON.parse(ev.data).method === "Page.loadEventFired") r();
}));
await send("Page.navigate", { url: pathToFileURL(resolve(file)).href });
await loaded;
if (mode === "zoom") {
  // Runs as DevTools, not as page script, so it works even with the
  // page's own scripts blocked.
  await send("Runtime.evaluate", { expression: "document.documentElement.style.fontSize='200%'" });
}
await sleep(1500); // Chart.js animation (wall clock) settles
const metrics = await send("Runtime.evaluate", {
  expression: "JSON.stringify([innerWidth, document.documentElement.scrollWidth])",
  returnByValue: true,
});
const [inner, scrollWidth] = JSON.parse(metrics.result.value);
const shot = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true });
writeFileSync(out, Buffer.from(shot.data, "base64"));
console.log(JSON.stringify({ file, mode, width: inner, scrollWidth, requests }));
clearTimeout(deadline);
ws.close();
cleanup();
process.exit(0);
