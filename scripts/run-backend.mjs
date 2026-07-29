#!/usr/bin/env node
/**
 * Start the backend, preferring the project's own virtualenv.
 *
 * Exists so `npm run backend` behaves the same on Windows and POSIX, and so it
 * uses `.venv` when present instead of whatever Python happens to be on PATH -
 * a mismatch there is the most common cause of "it works in the terminal but not
 * from the app".
 */

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BACKEND = path.join(ROOT, "backend");
const IS_WINDOWS = process.platform === "win32";

function resolvePython() {
  const venv = path.join(
    ROOT, ".venv", IS_WINDOWS ? "Scripts" : "bin", IS_WINDOWS ? "python.exe" : "python",
  );
  if (fs.existsSync(venv)) return venv;
  for (const candidate of IS_WINDOWS ? ["python", "py"] : ["python3", "python"]) {
    const probe = spawnSync(candidate, ["-c", "import papercreator"], {
      cwd: BACKEND,
      shell: false,
      timeout: 20000,
    });
    if (probe.status === 0) return candidate;
  }
  return null;
}

const python = resolvePython();
if (!python) {
  process.stderr.write(
    "Could not find a Python with papercreator installed.\n" +
      "Run `npm run setup` first, or install it manually:\n" +
      "  pip install -e backend\n",
  );
  process.exit(1);
}

const child = spawn(python, ["-m", "papercreator", ...process.argv.slice(2)], {
  cwd: BACKEND,
  stdio: "inherit",
  shell: false,
  env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8" },
});

// Forward Ctrl+C so uvicorn shuts down cleanly instead of being orphaned.
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}
child.on("exit", (code) => process.exit(code ?? 0));
