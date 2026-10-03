#!/usr/bin/env node
// vibecrafted-husky-template :: redact-output.mjs
//
// Reads stdin line-by-line, replaces matches of the canonical secret regex
// with `<REDACTED>`, writes to stdout. Used by hooks before archiving logs
// to .husky/warns/ so we don't leak credentials into the repo's git tree
// (warns/ is gitignored but the file still lives on disk).
//
// Vibecrafted with AI Agents by Vetcoders (c)2024-2026 LibraxisAI

import { createInterface } from "node:readline";

const SECRET_PATTERNS = [
  // OpenAI / similar
  /sk-(?:proj|live|test|dev)?-[A-Za-z0-9_-]{20,}/g,
  /sk_live_[A-Za-z0-9]{24,}/g,
  /pk_live_[A-Za-z0-9]{24,}/g,
  // GitHub
  /ghp_[A-Za-z0-9]{36,}/g,
  /gho_[A-Za-z0-9]{36,}/g,
  /github_pat_[A-Za-z0-9_]{20,}/g,
  // Slack
  /xox[baprs]-[A-Za-z0-9-]{10,}/g,
  // AWS
  /AKIA[0-9A-Z]{16}/g,
  /ASIA[0-9A-Z]{16}/g,
  // Google
  /AIza[0-9A-Za-z_-]{20,}/g,
  /ya29\.[0-9A-Za-z_-]{20,}/g,
  // PEM
  /-----BEGIN (RSA|OPENSSH|EC|DSA|PGP) PRIVATE KEY-----[\s\S]*?-----END \1 PRIVATE KEY-----/g,
  // product-specific service tokens
  /vista-[A-Za-z0-9_-]{20,}/g,
  // Authorization header
  /(Authorization:\s*Bearer\s+)[A-Za-z0-9._-]{16,}/gi,
];

const REPLACEMENTS = SECRET_PATTERNS.map((re, idx) => {
  // Bearer header keeps the prefix, the rest gets replaced wholesale.
  if (idx === SECRET_PATTERNS.length - 1) {
    return [re, "$1<REDACTED>"];
  }
  return [re, "<REDACTED>"];
});

function redactLine(line) {
  let out = line;
  for (const [re, replacement] of REPLACEMENTS) {
    out = out.replace(re, replacement);
  }
  return out;
}

const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", (line) => {
  process.stdout.write(`${redactLine(line)}\n`);
});
rl.on("close", () => {
  process.exit(0);
});
