#!/usr/bin/env node
/**
 * One-command setup: create a Python virtualenv, install the backend, install
 * the frontend, and run the backend's own diagnostics.
 *
 * Written in Node rather than shell so a single `npm run setup` works on Windows
 * and POSIX identically - the target platform is Windows, where a .sh script
 * would need Git Bash and a .bat would not run anywhere else.
 */

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import readline from "node:readline";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BACKEND = path.join(ROOT, "backend");
const VENV = path.join(ROOT, ".venv");
const IS_WINDOWS = process.platform === "win32";
const VENV_PYTHON = path.join(VENV, IS_WINDOWS ? "Scripts" : "bin", IS_WINDOWS ? "python.exe" : "python");

const args = new Set(process.argv.slice(2));
const withAnalysis = !args.has("--no-analysis");
const skipFrontend = args.has("--backend-only");

function log(message) {
  process.stdout.write(`\n\x1b[36m▸ ${message}\x1b[0m\n`);
}
function warn(message) {
  process.stdout.write(`\x1b[33m  ! ${message}\x1b[0m\n`);
}
function fail(message) {
  process.stdout.write(`\x1b[31m  ✕ ${message}\x1b[0m\n`);
}

function run(command, commandArgs, options = {}) {
  return new Promise((resolve) => {
    const executable = IS_WINDOWS && command === "npm" ? "npm.cmd" : command;
    const child = spawn(executable, commandArgs, {
      stdio: "inherit",
      shell: false,
      cwd: options.cwd ?? ROOT,
      env: { ...process.env, ...options.env },
    });
    child.on("exit", (code) => resolve(code ?? 1));
    child.on("error", () => resolve(1));
  });
}

/** Find a Python 3.10+ interpreter, probing each candidate for its version. */
function findSystemPython() {
  const candidates = IS_WINDOWS
    ? ["python", "python3", "py"]
    : ["python3", "python"];
  for (const candidate of candidates) {
    const probe = spawnSync(
      candidate,
      ["-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
      { encoding: "utf8", shell: false, timeout: 15000 },
    );
    const version = String(probe.stdout || "").trim();
    if (probe.status === 0 && /^\d+\.\d+$/.test(version)) {
      const [major, minor] = version.split(".").map(Number);
      if (major === 3 && minor >= 10) return { command: candidate, version };
      warn(`${candidate} is Python ${version}; 3.10 or newer is required`);
    }
  }
  return null;
}

async function confirm(question) {
  if (!process.stdin.isTTY) return true;
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const answer = await new Promise((resolve) => rl.question(`${question} [Y/n] `, resolve));
  rl.close();
  return !answer.trim().toLowerCase().startsWith("n");
}

async function main() {
  process.stdout.write("PaperCreator setup\n==================\n");

  const python = findSystemPython();
  if (!python) {
    fail("No Python 3.10+ found. Install it from https://www.python.org/downloads/");
    fail("On Windows, tick 'Add python.exe to PATH' during installation.");
    return 1;
  }
  process.stdout.write(`  python ${python.version} (${python.command})\n`);

  if (!fs.existsSync(VENV_PYTHON)) {
    log("Creating the virtual environment (.venv)");
    if ((await run(python.command, ["-m", "venv", VENV])) !== 0) {
      fail("Could not create the virtual environment.");
      return 1;
    }
  } else {
    process.stdout.write("  .venv already exists\n");
  }

  log("Installing the backend");
  await run(VENV_PYTHON, ["-m", "pip", "install", "--upgrade", "pip", "--quiet"]);
  const extras = withAnalysis ? "[analysis,export,dev]" : "[export,dev]";
  const installCode = await run(VENV_PYTHON, [
    "-m", "pip", "install", "-e", `${BACKEND}${extras}`,
  ]);
  if (installCode !== 0) {
    if (withAnalysis) {
      warn("Installation with the analysis extra failed.");
      warn("That extra pulls sentence-transformers and torch, which are large and");
      warn("occasionally conflict. The app works without it using TF-IDF vectors.");
      if (await confirm("Retry without the analysis extra?")) {
        if ((await run(VENV_PYTHON, ["-m", "pip", "install", "-e", `${BACKEND}[export,dev]`])) !== 0) {
          fail("Backend installation failed.");
          return 1;
        }
      } else {
        return 1;
      }
    } else {
      fail("Backend installation failed.");
      return 1;
    }
  }

  if (!skipFrontend) {
    log("Installing the frontend");
    if ((await run("npm", ["install", "--no-audit", "--no-fund"])) !== 0) {
      warn("Frontend installation failed. The backend can still be used on its own");
      warn("via `npm run backend` plus http://127.0.0.1:8765/api/docs");
    }
  }

  const envFile = path.join(ROOT, ".env");
  if (!fs.existsSync(envFile) && fs.existsSync(path.join(ROOT, ".env.example"))) {
    fs.copyFileSync(path.join(ROOT, ".env.example"), envFile);
    log("Created .env from .env.example");
    warn("Open .env and set PC_CONTACT_EMAIL (recommended) and an LLM API key");
    warn("if you want the agent features. Everything else works without keys.");
  }

  log("Running backend diagnostics");
  await run(VENV_PYTHON, ["-m", "papercreator", "--check"], { cwd: BACKEND });

  process.stdout.write(`
\x1b[32mSetup finished.\x1b[0m

  npm run dev        start the desktop app (backend starts automatically)
  npm run backend    start only the backend
  npm run test:backend   run the test suite

The app works with no API keys at all: retrieval uses free databases and the
landscape falls back to TF-IDF vectors. Add an LLM key in Settings > Models when
you want the writing agents.
`);
  return 0;
}

main().then((code) => process.exit(code));
