import { promises as fs } from "node:fs";
import os from "node:os";
import path from "node:path";

const NAME_PATTERN = /^papercreator-e2e-[A-Za-z0-9]{6}$/;
const RETRYABLE = new Set(["EBUSY", "EPERM", "ENOTEMPTY"]);

function parseArguments(argv) {
  const options = { apply: false, json: false, minAgeHours: 24 };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--apply") options.apply = true;
    else if (value === "--json") options.json = true;
    else if (value === "--min-age-hours") {
      const raw = argv[index + 1];
      index += 1;
      const parsed = Number(raw);
      if (!Number.isFinite(parsed) || parsed < 1) {
        throw new Error("--min-age-hours must be a finite number greater than or equal to 1");
      }
      options.minAgeHours = parsed;
    } else if (value === "--help" || value === "-h") {
      console.log(
        "Usage: npm run cleanup:e2e -- [--apply] [--min-age-hours HOURS] [--json]\n" +
        "Defaults to a dry run and only considers papercreator-e2e-XXXXXX directories " +
        "directly below the operating-system temporary directory.",
      );
      process.exit(0);
    } else {
      throw new Error(`unknown argument: ${value}`);
    }
  }
  return options;
}

async function inventory(directory) {
  let files = 0;
  let bytes = 0;
  let latestMtimeMs = 0;
  const pending = [directory];
  while (pending.length > 0) {
    const current = pending.pop();
    const entries = await fs.readdir(current, { withFileTypes: true });
    for (const entry of entries) {
      const candidate = path.join(current, entry.name);
      const stat = await fs.lstat(candidate);
      latestMtimeMs = Math.max(latestMtimeMs, stat.mtimeMs);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) pending.push(candidate);
      else if (entry.isFile()) {
        files += 1;
        bytes += stat.size;
      }
    }
  }
  return { files, bytes, latestMtimeMs };
}

async function removeWithRetry(directory) {
  for (let attempt = 0; attempt < 12; attempt += 1) {
    try {
      await fs.rm(directory, { recursive: true, force: true });
      return;
    } catch (error) {
      if (!RETRYABLE.has(String(error?.code)) || attempt === 11) throw error;
      await new Promise((resolve) => setTimeout(resolve, Math.min(1000, 100 + attempt * 100)));
    }
  }
}

async function main() {
  const options = parseArguments(process.argv.slice(2));
  const temporaryRoot = await fs.realpath(os.tmpdir());
  const entries = await fs.readdir(temporaryRoot, { withFileTypes: true });
  const cutoffMs = Date.now() - options.minAgeHours * 60 * 60 * 1000;
  const candidates = [];
  const failed = [];

  for (const entry of entries) {
    if (!entry.isDirectory() || entry.isSymbolicLink() || !NAME_PATTERN.test(entry.name)) continue;
    const candidate = path.join(temporaryRoot, entry.name);
    try {
      const resolved = await fs.realpath(candidate);
      if (path.dirname(resolved) !== temporaryRoot || !NAME_PATTERN.test(path.basename(resolved))) {
        throw new Error(`resolved path escaped the temporary boundary: ${resolved}`);
      }
      const rootStat = await fs.lstat(resolved);
      if (!rootStat.isDirectory() || rootStat.isSymbolicLink()) continue;
      const details = await inventory(resolved);
      const latestMtimeMs = Math.max(rootStat.mtimeMs, details.latestMtimeMs);
      if (latestMtimeMs > cutoffMs) continue;
      const record = {
        path: resolved,
        files: details.files,
        bytes: details.bytes,
        latest_mtime: new Date(latestMtimeMs).toISOString(),
        action: options.apply ? "removed" : "would_remove",
      };
      if (options.apply) await removeWithRetry(resolved);
      candidates.push(record);
    } catch (error) {
      failed.push({ path: candidate, error: String(error?.message || error) });
    }
  }

  const report = {
    schema_version: 1,
    mode: options.apply ? "apply" : "dry_run",
    temporary_root: temporaryRoot,
    min_age_hours: options.minAgeHours,
    candidates,
    failed,
  };
  if (options.json) console.log(JSON.stringify(report, null, 2));
  else {
    console.log(
      `${options.apply ? "Removed" : "Would remove"} ${candidates.length} stale Electron E2E ` +
      `director${candidates.length === 1 ? "y" : "ies"}; ${failed.length} failed.`,
    );
    for (const item of candidates) {
      console.log(`  ${item.action.padEnd(12)} ${item.path} (${item.files} files, ${item.bytes} bytes)`);
    }
    for (const item of failed) console.error(`  failed       ${item.path}: ${item.error}`);
  }
  if (failed.length > 0) process.exitCode = 1;
}

main().catch((error) => {
  console.error(`E2E cleanup failed: ${error?.message || error}`);
  process.exitCode = 1;
});
