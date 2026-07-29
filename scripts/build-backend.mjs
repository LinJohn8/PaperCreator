#!/usr/bin/env node
/** Build the Python service as a self-contained PyInstaller onedir runtime. */

import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BACKEND = path.join(ROOT, "backend");
const ENTRY = path.join(ROOT, "scripts", "backend_entry.py");
const BUILD_ROOT = path.join(ROOT, "build", "backend-pyinstaller");
const DIST_ROOT = path.join(ROOT, "build", "backend-runtime");
const IS_WINDOWS = process.platform === "win32";
const PYTHON = path.join(
  ROOT,
  ".venv",
  IS_WINDOWS ? "Scripts" : "bin",
  IS_WINDOWS ? "python.exe" : "python",
);

function assertInsideWorkspace(target) {
  const resolved = path.resolve(target);
  if (!resolved.startsWith(`${ROOT}${path.sep}`) || resolved === ROOT) {
    throw new Error(`refusing to modify a path outside the workspace: ${resolved}`);
  }
}

function run(command, args) {
  return new Promise((resolve) => {
    const child = spawn(command, args, {
      cwd: ROOT,
      stdio: "inherit",
      shell: false,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    });
    child.on("error", (error) => {
      process.stderr.write(`Could not start ${command}: ${error.message}\n`);
      resolve(1);
    });
    child.on("exit", (code) => resolve(code ?? 1));
  });
}

async function main() {
  if (!fs.existsSync(PYTHON)) {
    process.stderr.write("Project virtualenv not found. Run `npm run setup` first.\n");
    return 1;
  }

  const probe = await run(PYTHON, ["-c", "import PyInstaller"]);
  if (probe !== 0) {
    process.stderr.write(
      "PyInstaller is not installed. Run:\n" +
      `  ${PYTHON} -m pip install -e \"${BACKEND}[package]\"\n`,
    );
    return 1;
  }

  for (const target of [BUILD_ROOT, DIST_ROOT]) {
    assertInsideWorkspace(target);
    fs.rmSync(target, { recursive: true, force: true });
    fs.mkdirSync(target, { recursive: true });
  }

  const resources = path.join(BACKEND, "papercreator", "resources");
  const addData = `${resources}${path.delimiter}papercreator/resources`;
  const code = await run(PYTHON, [
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onedir",
    "--console",
    "--name", "papercreator-backend",
    "--paths", BACKEND,
    "--add-data", addData,
    "--exclude-module", "pytest",
    "--exclude-module", "_pytest",
    "--exclude-module", "tests",
    "--distpath", DIST_ROOT,
    "--workpath", BUILD_ROOT,
    "--specpath", BUILD_ROOT,
    ENTRY,
  ]);
  if (code !== 0) return code;

  const executable = path.join(
    DIST_ROOT,
    "papercreator-backend",
    IS_WINDOWS ? "papercreator-backend.exe" : "papercreator-backend",
  );
  if (!fs.existsSync(executable)) {
    process.stderr.write(`PyInstaller finished but ${executable} is missing.\n`);
    return 1;
  }
  process.stdout.write(`\nBackend runtime ready: ${executable}\n`);
  return 0;
}

main().then((code) => process.exit(code));
