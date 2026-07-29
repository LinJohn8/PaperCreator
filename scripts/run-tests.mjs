#!/usr/bin/env node
/**
 * Run the backend test suite.
 *
 * Tests run against a temporary PAPERCREATOR_HOME so they never touch the real
 * database, workspace or settings. Pass `--live` to also run the tests that hit
 * real scholarly APIs (excluded by default: they are slow and depend on the
 * network being up and the providers being reachable).
 */

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BACKEND = path.join(ROOT, "backend");
const IS_WINDOWS = process.platform === "win32";

const args = process.argv.slice(2);
const live = args.includes("--live");
const pytestArgs = args.filter((arg) => arg !== "--live");

function resolvePython() {
  const venv = path.join(
    ROOT, ".venv", IS_WINDOWS ? "Scripts" : "bin", IS_WINDOWS ? "python.exe" : "python",
  );
  if (fs.existsSync(venv)) return venv;
  for (const candidate of IS_WINDOWS ? ["python", "py"] : ["python3", "python"]) {
    if (spawnSync(candidate, ["-c", "import pytest"], { shell: false, timeout: 20000 }).status === 0) {
      return candidate;
    }
  }
  return null;
}

const python = resolvePython();
if (!python) {
  process.stderr.write("pytest is not installed. Run `npm run setup`.\n");
  process.exit(1);
}

// Isolated home per run, so a test can never write into the real install.
const home = fs.mkdtempSync(path.join(os.tmpdir(), "pc-test-"));
const selection = live ? [] : ["-m", "not live"];

const child = spawn(
  python,
  ["-m", "pytest", "-v", ...selection, ...pytestArgs],
  {
    cwd: BACKEND,
    stdio: "inherit",
    shell: false,
    env: {
      ...process.env,
      PAPERCREATOR_HOME: home,
      PYTHONIOENCODING: "utf-8",
      // Keep tests off the network unless explicitly asked.
      PC_OFFLINE_MODELS: live ? "0" : "1",
    },
  },
);

child.on("exit", (code) => {
  try {
    fs.rmSync(home, { recursive: true, force: true });
  } catch {
    /* a leftover temp directory is harmless */
  }
  if (!live) {
    process.stdout.write(
      "\nLive-API tests were skipped. Run `npm run test:backend -- --live` to include them.\n",
    );
  }
  process.exit(code ?? 0);
});
