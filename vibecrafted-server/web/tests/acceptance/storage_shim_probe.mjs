#!/usr/bin/env node
// Behavioural proof for the Web Storage stand-in the server injects into the
// served Loctree report (`STORAGE_SHIM` in `src/tools.rs`).
//
// The document runs under `sandbox allow-scripts` with an opaque origin, where
// the real `window.localStorage` / `window.sessionStorage` getters throw. The
// stand-in must then give the report two *independent* in-memory storages —
// one per name — and must leave a usable real storage untouched. This script
// evaluates the exact injected script in a Node `vm` context with nothing but
// Node built-ins and asserts the behaviour a browser would observe.
//
// Usage:
//   node storage_shim_probe.mjs <report.html | http://host/structure/report/> [--json out.json]
//   node storage_shim_probe.mjs - < report.html
//
// Exit code 0 when every assertion holds, 1 otherwise. The JSON verdict is
// printed (and optionally written) either way.

import { readFileSync, writeFileSync } from "node:fs";
import vm from "node:vm";

const args = process.argv.slice(2);
const source = args.find((a) => !a.startsWith("--"));
if (!source) {
  console.error("usage: storage_shim_probe.mjs <report.html|url|-> [--json <file>]");
  process.exit(2);
}
const jsonOut = args.includes("--json") ? args[args.indexOf("--json") + 1] : null;

const html =
  source === "-"
    ? readFileSync(0, "utf8")
    : /^https?:\/\//.test(source)
      ? await (await fetch(source)).text()
      : readFileSync(source, "utf8");

const match = html.match(
  /<script data-vibecrafted="storage-shim">([\s\S]*?)<\/script>/,
);
if (!match) {
  console.error("no <script data-vibecrafted=\"storage-shim\"> in the document");
  process.exit(2);
}
const shim = match[1];

/** A window whose storage getters throw the sandbox's SecurityError. */
function opaqueWindow() {
  const window = {};
  for (const name of ["localStorage", "sessionStorage"]) {
    Object.defineProperty(window, name, {
      configurable: true,
      enumerable: true,
      get() {
        const error = new Error(
          `Failed to read the '${name}' property from 'Window': The document is sandboxed and lacks the 'allow-same-origin' flag.`,
        );
        error.name = "SecurityError";
        throw error;
      },
    });
  }
  return window;
}

/** A minimal working Storage, standing in for a same-origin browser storage. */
function realStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(String(k)) ? map.get(String(k)) : null),
    setItem: (k, v) => map.set(String(k), String(v)),
    removeItem: (k) => map.delete(String(k)),
    clear: () => map.clear(),
    key: (n) => [...map.keys()][n] ?? null,
    get length() {
      return map.size;
    },
  };
}

function run(window) {
  const context = vm.createContext({ window });
  vm.runInContext(shim, context, { filename: "storage-shim.js" });
  return window;
}

const failures = [];
const check = (name, ok, detail) => {
  if (!ok) failures.push({ name, detail });
  return ok;
};

// --- Scenario 1: opaque origin, both getters throw -> two independent stand-ins.
const opaque = run(opaqueWindow());
const local = opaque.localStorage;
const session = opaque.sessionStorage;
const asObject = (s) => s && typeof s === "object";
check("installed.local", asObject(local), typeof local);
check("installed.session", asObject(session), typeof session);
check("distinct.objects", local !== session);

local.setItem("only-local", "value");
check("separation.session-does-not-see-local", session.getItem("only-local") === null, {
  session: session.getItem("only-local"),
});
session.setItem("only-session", "other");
check("separation.local-does-not-see-session", local.getItem("only-local") === "value" && local.getItem("only-session") === null, {
  local: [local.getItem("only-local"), local.getItem("only-session")],
});
check("length.independent", local.length === 1 && session.length === 1, {
  local: local.length,
  session: session.length,
});
check("key.independent", local.key(0) === "only-local" && session.key(0) === "only-session", {
  local: local.key(0),
  session: session.key(0),
});

// get / set / remove / length on one storage, the other unaffected.
local.setItem("a", 1);
local.setItem("b", "2");
check("set.coerces-to-string", local.getItem("a") === "1" && local.getItem("b") === "2");
check("length.counts", local.length === 3 && session.length === 1);
local.removeItem("a");
check("remove.drops-key", local.getItem("a") === null && local.length === 2);
check("remove.missing-is-noop", (local.removeItem("nope"), local.length === 2));
check("key.out-of-range", local.key(99) === null && session.key(1) === null);
check("get.missing-is-null", local.getItem("never") === null && session.getItem("a") === null);

// clear() on one storage must not empty the other.
local.clear();
check("clear.local-empties-local", local.length === 0 && local.getItem("only-local") === null);
check("clear.local-keeps-session", session.length === 1 && session.getItem("only-session") === "other", {
  session: [session.length, session.getItem("only-session")],
});
local.setItem("after-clear", "x");
session.clear();
check("clear.session-keeps-local", local.length === 1 && local.getItem("after-clear") === "x" && session.length === 0, {
  local: [local.length, local.getItem("after-clear")],
  session: session.length,
});

// The stand-in is a stable property the report can keep referring to.
check("stable.identity", opaque.localStorage === local && opaque.sessionStorage === session);
check("no-prototype-keys", local.getItem("constructor") === null && local.getItem("__proto__") === null);

// --- Scenario 2: a usable real storage is left exactly as it was.
const real = { localStorage: realStorage(), sessionStorage: realStorage() };
const kept = run({ localStorage: real.localStorage, sessionStorage: real.sessionStorage });
check("real.local-untouched", kept.localStorage === real.localStorage);
check("real.session-untouched", kept.sessionStorage === real.sessionStorage);
check("real.probe-leaves-nothing", real.localStorage.length === 0 && real.sessionStorage.length === 0);

// --- Scenario 3: only one storage throws -> only that one is replaced.
const mixedWindow = opaqueWindow();
const workingSession = realStorage();
Object.defineProperty(mixedWindow, "sessionStorage", { value: workingSession, configurable: true, writable: true });
const mixed = run(mixedWindow);
check("mixed.local-replaced", asObject(mixed.localStorage) && mixed.localStorage !== workingSession);
check("mixed.session-kept", mixed.sessionStorage === workingSession);
mixed.localStorage.setItem("k", "v");
check("mixed.no-crosstalk", workingSession.getItem("k") === null && mixed.localStorage.getItem("k") === "v");

const record = {
  source,
  shimBytes: shim.length,
  failures,
  pass: failures.length === 0,
};
const output = JSON.stringify(record, null, 2);
console.log(output);
if (jsonOut) writeFileSync(jsonOut, output);
process.exit(record.pass ? 0 : 1);
