#!/usr/bin/env node
// Real-browser acceptance for the served Loctree report (`/structure/report`).
//
// Drives a Chromium build over the DevTools protocol with nothing but Node's
// built-in `fetch` and `WebSocket` (Node >= 22): no npm dependency, no
// Playwright download. Records what a human would check by hand:
//
//   * every sub-resource the report asks for answers 2xx (relative assets
//     resolve against the document URL);
//   * `window.localStorage` is usable from the sandboxed document (the report's
//     scripts touch it at top level and die otherwise), and `localStorage` /
//     `sessionStorage` are two independent storages (a key written to one is
//     invisible to the other; clearing one leaves the other intact);
//   * a real mouse click on the "Graph" sidebar item switches the active panel
//     and, when the report carries graph data, Cytoscape draws into it
//     (`graphDataDeclared` in the verdict says which case ran);
//   * the theme toggle flips `<html class>` without throwing;
//   * no uncaught exception and no console error during the run.
//
// Usage:
//   node loctree_report_browser.mjs http://127.0.0.1:3024/structure/report [--json out.json]
//   CHROMIUM=/Applications/Chromium.app/Contents/MacOS/Chromium node loctree_report_browser.mjs <url>
//
// Exit code 0 when every check passes, 1 otherwise. The JSON verdict is printed
// (and optionally written) either way, so a failing run still documents itself.

import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const args = process.argv.slice(2);
const url = args.find((a) => !a.startsWith("--"));
if (!url) {
  console.error("usage: loctree_report_browser.mjs <report-url> [--json <file>]");
  process.exit(2);
}
const jsonOut = args.includes("--json") ? args[args.indexOf("--json") + 1] : null;
// Optional free-form expression evaluated at the end, recorded under `probe`
// (diagnostics only; never part of the verdict).
const probe = args.includes("--probe") ? args[args.indexOf("--probe") + 1] : null;

const CHROMIUM =
  process.env.CHROMIUM ||
  [
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
  ].find((p) => {
    try {
      return statSync(p).isFile();
    } catch {
      return false;
    }
  });

const profile = mkdtempSync(join(tmpdir(), "vc-report-acceptance-"));
const chrome = spawn(
  CHROMIUM,
  [
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-gpu",
    "--remote-debugging-port=0",
    `--user-data-dir=${profile}`,
    "about:blank",
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);

const wsEndpoint = await new Promise((resolve, reject) => {
  let buffer = "";
  const timer = setTimeout(() => reject(new Error("chromium did not announce DevTools")), 20000);
  chrome.stderr.on("data", (chunk) => {
    buffer += chunk.toString();
    const match = buffer.match(/DevTools listening on (ws:\/\/\S+)/);
    if (match) {
      clearTimeout(timer);
      resolve(match[1]);
    }
  });
  chrome.on("exit", (code) => reject(new Error(`chromium exited early (${code})`)));
});

let nextId = 0;
const pending = new Map();
const sessionHandlers = new Map();
const ws = new WebSocket(wsEndpoint);
await new Promise((resolve, reject) => {
  ws.onopen = resolve;
  ws.onerror = reject;
});
ws.onmessage = (event) => {
  const message = JSON.parse(event.data);
  if (message.id !== undefined && pending.has(message.id)) {
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    message.error ? reject(new Error(message.error.message)) : resolve(message.result);
    return;
  }
  if (message.method && message.sessionId && sessionHandlers.has(message.sessionId)) {
    sessionHandlers.get(message.sessionId)(message);
  }
};
const send = (method, params = {}, sessionId) =>
  new Promise((resolve, reject) => {
    const id = ++nextId;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params, sessionId }));
  });

const { targetId } = await send("Target.createTarget", { url: "about:blank" });
const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });

const record = {
  url,
  chromium: CHROMIUM,
  finalUrl: null,
  responses: [],
  failedRequests: [],
  consoleErrors: [],
  exceptions: [],
  checks: {},
};
sessionHandlers.set(sessionId, (message) => {
  switch (message.method) {
    case "Network.responseReceived": {
      const { response, type } = message.params;
      record.responses.push({ url: response.url, status: response.status, type });
      break;
    }
    case "Network.loadingFailed":
      record.failedRequests.push({
        requestId: message.params.requestId,
        errorText: message.params.errorText,
        blocked: message.params.blockedReason || null,
      });
      break;
    case "Runtime.exceptionThrown": {
      const details = message.params.exceptionDetails;
      record.exceptions.push(
        details.exception?.description || details.text || JSON.stringify(details),
      );
      break;
    }
    case "Runtime.consoleAPICalled":
      if (message.params.type === "error") {
        record.consoleErrors.push(
          message.params.args.map((a) => a.description ?? a.value ?? "").join(" "),
        );
      }
      break;
    case "Log.entryAdded":
      if (message.params.entry.level === "error") {
        record.consoleErrors.push(`${message.params.entry.source}: ${message.params.entry.text}`);
      }
      break;
  }
});

await send("Page.enable", {}, sessionId);
await send("Runtime.enable", {}, sessionId);
await send("Network.enable", {}, sessionId);
await send("Log.enable", {}, sessionId);

const loaded = new Promise((resolve) => {
  const previous = sessionHandlers.get(sessionId);
  sessionHandlers.set(sessionId, (message) => {
    previous(message);
    if (message.method === "Page.loadEventFired") resolve();
  });
});
await send("Page.navigate", { url }, sessionId);
await Promise.race([loaded, new Promise((r) => setTimeout(r, 15000))]);
await new Promise((r) => setTimeout(r, 1500));

const evaluate = async (expression) => {
  const { result, exceptionDetails } = await send(
    "Runtime.evaluate",
    { expression, returnByValue: true, awaitPromise: true },
    sessionId,
  );
  if (exceptionDetails) {
    throw new Error(exceptionDetails.exception?.description || exceptionDetails.text);
  }
  return result.value;
};

record.finalUrl = await evaluate("location.href");
record.checks.storage = await evaluate(
  `(() => { try { localStorage.setItem('vc-acceptance', '1'); const v = localStorage.getItem('vc-acceptance'); localStorage.removeItem('vc-acceptance'); return { ok: v === '1' }; } catch (e) { return { ok: false, error: e.name + ': ' + e.message }; } })()`,
);
// The two storages must be independent objects. Only `sessionStorage` is
// cleared here: the report keeps its own state (theme) in `localStorage`.
record.checks.storageIsolation = await evaluate(
  `(() => { try { const before = [localStorage.length, sessionStorage.length]; localStorage.setItem('vc-only-local', 'L'); sessionStorage.setItem('vc-only-session', 'S'); const crossed = sessionStorage.getItem('vc-only-local') !== null || localStorage.getItem('vc-only-session') !== null; const grew = localStorage.length === before[0] + 1 && sessionStorage.length === before[1] + 1; sessionStorage.clear(); const localKept = localStorage.getItem('vc-only-local') === 'L' && localStorage.length === before[0] + 1 && sessionStorage.length === 0; localStorage.removeItem('vc-only-local'); const restored = localStorage.length === before[0] && localStorage.getItem('vc-only-local') === null; const distinct = localStorage !== sessionStorage; return { ok: distinct && !crossed && grew && localKept && restored, distinct, crossed, grew, localKept, restored, before }; } catch (e) { return { ok: false, error: e.name + ': ' + e.message }; } })()`,
);
record.checks.cytoscapeGlobal = await evaluate("typeof cytoscape");
record.checks.initialActivePanel = await evaluate(
  "document.querySelector('.tab-panel.active')?.dataset.tabName ?? null",
);

// A real click on the Graph sidebar item, at its on-screen centre.
const graphBox = await evaluate(
  `(() => { const b = document.querySelector('.sidebar-nav .nav-item[data-tab="graph"]'); if (!b) return null; b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`,
);
record.checks.graphButtonFound = graphBox !== null;
if (graphBox) {
  for (const type of ["mousePressed", "mouseReleased"]) {
    await send(
      "Input.dispatchMouseEvent",
      { type, x: graphBox.x, y: graphBox.y, button: "left", clickCount: 1 },
      sessionId,
    );
  }
  await new Promise((r) => setTimeout(r, 1500));
}
record.checks.activePanelAfterGraphClick = await evaluate(
  "document.querySelector('.tab-panel.active')?.dataset.tabName ?? null",
);
record.checks.graphCanvasDrawn = await evaluate(
  `(() => { const panel = document.querySelector('.tab-panel[data-tab-name="graph"]'); if (!panel) return false; const canvas = panel.querySelector('canvas'); return !!canvas && canvas.width > 0; })()`,
);
// Diagnostics for a human reading a failed run: what the graph panel holds.
record.checks.graphPanel = await evaluate(
  `(() => { const panel = document.querySelector('.tab-panel[data-tab-name="graph"]'); if (!panel) return null; const r = panel.getBoundingClientRect(); return { visible: r.width > 0 && r.height > 0, rect: { w: r.width, h: r.height }, canvases: panel.querySelectorAll('canvas').length, documentCanvases: document.querySelectorAll('canvas').length, graphsDeclared: (window.__LOCTREE_GRAPHS || []).length, containers: Array.from(panel.querySelectorAll('[id]')).slice(0, 12).map((e) => e.id + ':' + e.tagName.toLowerCase() + ':' + e.children.length), text: panel.innerText.replace(/\\s+/g, ' ').slice(0, 400) }; })()`,
);

// Cytoscape has to execute from the served assets, not merely load: visit the
// other graph-bearing tabs (Twins, Crowds) with real clicks and count canvases.
const clickTab = async (name) => {
  const box = await evaluate(
    `(() => { const b = document.querySelector('.sidebar-nav .nav-item[data-tab="${name}"]'); if (!b) return null; b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; })()`,
  );
  if (!box) return false;
  for (const type of ["mousePressed", "mouseReleased"]) {
    await send(
      "Input.dispatchMouseEvent",
      { type, x: box.x, y: box.y, button: "left", clickCount: 1 },
      sessionId,
    );
  }
  await new Promise((r) => setTimeout(r, 1200));
  return (await evaluate("document.querySelector('.tab-panel.active')?.dataset.tabName ?? null")) === name;
};
// The Twins and Crowds graphs sit behind collapsible section headers and are
// built on first expansion — click those headers the way a reader would.
const clickAt = async (selector) => {
  const box = await evaluate(
    `(() => { const b = document.querySelector(${JSON.stringify(selector)}); if (!b) return null; b.scrollIntoView({block: 'center'}); const r = b.getBoundingClientRect(); return r.width > 0 && r.height > 0 ? { x: r.x + r.width / 2, y: r.y + r.height / 2 } : null; })()`,
  );
  if (!box) return false;
  for (const type of ["mousePressed", "mouseReleased"]) {
    await send(
      "Input.dispatchMouseEvent",
      { type, x: box.x, y: box.y, button: "left", clickCount: 1 },
      sessionId,
    );
  }
  await new Promise((r) => setTimeout(r, 1500));
  return true;
};
record.checks.twinsTabSwitched = await clickTab("twins");
record.checks.twinsGraphExpanded = await clickAt(
  '.twins-section-header[data-toggle="twins-exact-content"]',
);
record.checks.crowdsTabSwitched = await clickTab("crowds");
record.checks.crowdsGraphExpanded = await clickAt(
  '.crowds-section-header[data-toggle="crowds-graph-content"]',
);
record.checks.graphData = await evaluate(
  `({ graphs: (window.__LOCTREE_GRAPHS || []).length, twins: window.__TWINS_DATA__ ? 1 : 0, crowds: Array.isArray(window.__CROWDS_DATA__) ? window.__CROWDS_DATA__.length : (window.__CROWDS_DATA__ ? 1 : 0) })`,
);
record.checks.documentCanvases = await evaluate("document.querySelectorAll('canvas').length");

record.checks.theme = await evaluate(
  `(() => { const before = document.documentElement.className; const t = document.querySelector('[data-role="theme-toggle"]'); if (!t) return { ok: false, error: 'no toggle' }; t.click(); const after = document.documentElement.className; return { ok: before !== after, before, after }; })()`,
);

await new Promise((r) => setTimeout(r, 300));
if (probe) {
  try {
    record.probe = await evaluate(probe);
  } catch (error) {
    record.probe = { error: String(error) };
  }
}

const subresources = record.responses.filter((r) => r.type !== "Document");
record.checks.subresourceFailures = subresources.filter((r) => r.status >= 400);
record.checks.documentStatus = record.responses.find((r) => r.type === "Document")?.status ?? null;

const verdict = {
  documentOk: record.checks.documentStatus === 200,
  assetsOk:
    record.checks.subresourceFailures.length === 0 &&
    subresources.some((r) => r.type === "Script" && r.status === 200),
  storageOk: record.checks.storage.ok === true,
  storageIsolated: record.checks.storageIsolation.ok === true,
  // The panel must switch on a real click. Cytoscape can only draw when the
  // report carries graph data (`window.__LOCTREE_GRAPHS`); a report of a tiny
  // fixture has an empty Graph panel by construction, which is recorded, not
  // failed.
  graphTabOk:
    record.checks.graphButtonFound &&
    record.checks.activePanelAfterGraphClick === "graph" &&
    (record.checks.graphCanvasDrawn === true ||
      (record.checks.graphPanel?.graphsDeclared ?? 0) === 0),
  themeOk: record.checks.theme.ok === true,
  // Cytoscape must have drawn somewhere when any tab carries graph data.
  cytoscapeRenders:
    record.checks.cytoscapeGlobal === "function" &&
    (record.checks.documentCanvases > 0 ||
      Object.values(record.checks.graphData).every((n) => n === 0)),
  noExceptions: record.exceptions.length === 0,
  noConsoleErrors: record.consoleErrors.length === 0,
};
record.graphDataDeclared = Object.values(record.checks.graphData).some((n) => n > 0);
record.verdict = verdict;
record.pass = Object.values(verdict).every(Boolean);

const output = JSON.stringify(record, null, 2);
console.log(output);
if (jsonOut) writeFileSync(jsonOut, output);

ws.close();
chrome.kill("SIGTERM");
await new Promise((r) => chrome.on("exit", r));
rmSync(profile, { recursive: true, force: true });
process.exit(record.pass ? 0 : 1);
