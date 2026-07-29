import { _electron as electron } from "@playwright/test";
import { spawn } from "node:child_process";
import { promises as fs } from "node:fs";
import { createServer } from "node:net";
import os from "node:os";
import path from "node:path";
import process from "node:process";

const repositoryRoot = path.resolve(import.meta.dirname, "..");
const installer = path.join(
  repositoryRoot,
  "apps",
  "desktop",
  "release",
  "PaperCreator-Setup.exe",
);
const uninstallRegistry = "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

async function exists(candidate) {
  return fs.access(candidate).then(() => true, () => false);
}

async function run(executable, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, {
      cwd: options.cwd ?? repositoryRoot,
      env: options.env ?? process.env,
      shell: false,
      windowsHide: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    child.stdout.on("data", (chunk) => stdout.push(chunk));
    child.stderr.on("data", (chunk) => stderr.push(chunk));
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      const result = {
        code,
        signal,
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8"),
      };
      if (code === 0) resolve(result);
      else reject(new Error(
        `${path.basename(executable)} exited ${code ?? signal}\n${result.stdout}\n${result.stderr}`,
      ));
    });
  });
}

async function paperCreatorRegistration() {
  try {
    const result = await run("reg.exe", [
      "query",
      uninstallRegistry,
      "/s",
      "/f",
      "PaperCreator",
      "/d",
    ]);
    return result.stdout;
  } catch (error) {
    if (String(error.message).includes("exited 1")) return "";
    throw error;
  }
}

async function reservePort() {
  const server = createServer();
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert(address && typeof address !== "string", "could not reserve a loopback port");
  await new Promise((resolve, reject) =>
    server.close((error) => error ? reject(error) : resolve()),
  );
  return address.port;
}

async function poll(check, description, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const value = await check();
      if (value) return value;
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`timed out waiting for ${description}${lastError ? `: ${lastError}` : ""}`);
}

async function request(origin, method, route, body) {
  const response = await fetch(`${origin}${route}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(20_000),
  });
  const text = await response.text();
  let payload;
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    payload = { raw: text };
  }
  if (!response.ok) {
    throw new Error(`${method} ${route} returned ${response.status}: ${text}`);
  }
  return payload;
}

async function launchInstalled(executable, workbench) {
  const port = await reservePort();
  const env = { ...process.env };
  Object.assign(env, {
    PAPERCREATOR_WORKBENCH: workbench,
    PC_PORT: String(port),
    PC_OFFLINE_MODELS: "1",
    PC_E2E: "1",
    ELECTRON_DISABLE_SECURITY_WARNINGS: "true",
  });
  delete env.ELECTRON_RUN_AS_NODE;

  const application = await electron.launch({
    executablePath: executable,
    args: [],
    cwd: path.dirname(executable),
    env,
    timeout: 60_000,
  });
  const page = await application.firstWindow({ timeout: 60_000 });
  const info = await poll(async () => {
    const current = await page.evaluate(() => window.papercreator.appInfo());
    return current.backendRunning && current.bundledBackendExists ? current : null;
  }, "installed bundled backend");
  await poll(async () => {
    const response = await fetch(`${info.backendOrigin}/api/system/health`, {
      signal: AbortSignal.timeout(5_000),
    });
    return response.ok;
  }, "installed backend health");
  return { application, page, info, port };
}

function safeTemporaryRoot(candidate) {
  const resolved = path.resolve(candidate);
  const allowed = `${path.resolve(os.tmpdir())}${path.sep}`;
  assert(
    resolved.startsWith(allowed) && path.basename(resolved).startsWith("papercreator-installer-e2e-"),
    `refusing to clean an unsafe installer smoke path: ${resolved}`,
  );
  return resolved;
}

async function closeApplication(state) {
  if (!state?.application) return;
  await state.application.close().catch(() => {});
  state.application = null;
  if (state.port) {
    await poll(async () => {
      try {
        const response = await fetch(`http://127.0.0.1:${state.port}/api/system/health`, {
          signal: AbortSignal.timeout(750),
        });
        return !response.ok;
      } catch {
        return true;
      }
    }, "installed backend shutdown", 20_000);
  }
}

async function findUninstaller(installDirectory) {
  const entries = await fs.readdir(installDirectory);
  const name = entries.find((entry) => /^uninstall.*\.exe$/i.test(entry));
  assert(name, `uninstaller was not found below ${installDirectory}`);
  return path.join(installDirectory, name);
}

async function main() {
  assert(process.platform === "win32", "installer smoke is Windows-only");
  assert(await exists(installer), `installer is missing: ${installer}; run npm run package first`);
  const preexisting = await paperCreatorRegistration();
  assert(
    !preexisting,
    "PaperCreator is already registered for this Windows user; refusing to overwrite a real installation",
  );

  const root = safeTemporaryRoot(
    await fs.mkdtemp(path.join(os.tmpdir(), "papercreator-installer-e2e-")),
  );
  const installDirectory = path.join(root, "安装 目录");
  const workbench = path.join(root, "研究 工作台");
  const managed = path.join(workbench, ".papercreator");
  const executable = path.join(installDirectory, "PaperCreator.exe");
  const state = { application: null, port: 0 };
  let installed = false;
  let uninstalled = false;

  try {
    await fs.mkdir(workbench, { recursive: true });
    process.stdout.write(`Installing into ${installDirectory}\n`);
    await run(installer, ["/S", `/D=${installDirectory}`]);
    installed = true;
    assert(await exists(executable), "silent installer did not create PaperCreator.exe");
    assert(await paperCreatorRegistration(), "NSIS did not create the per-user uninstall registration");

    Object.assign(state, await launchInstalled(executable, workbench));
    const expectedManaged = path.resolve(managed).toLowerCase();
    assert(path.resolve(state.info.workbench).toLowerCase() === path.resolve(workbench).toLowerCase(), "installed app selected the wrong workbench");
    assert(path.resolve(state.info.managedDirectory).toLowerCase() === expectedManaged, "installed app selected the wrong managed directory");
    assert(state.info.isDev === false, "installed app incorrectly reports development mode");
    assert(path.resolve(state.info.backendExecutable).startsWith(path.resolve(installDirectory)), "installed app did not use its bundled backend");

    const workbenchInfo = await request(state.info.backendOrigin, "GET", "/api/workbench");
    assert(path.resolve(workbenchInfo.managed_directory).toLowerCase() === expectedManaged, "backend managed root differs from Electron");
    const expectedKinds = [
      "idea",
      "reference_paper",
      "own_paper",
      "code_project",
      "dataset",
      "supplementary",
      "inbox",
    ];
    assert(
      expectedKinds.every((kind) => workbenchInfo.categories.some((entry) => entry.kind === kind)),
      "installed workbench does not expose all seven input categories",
    );

    const stamp = Date.now();
    const idea = await request(state.info.backendOrigin, "POST", "/api/workbench/resources", {
      kind: "idea",
      title: `Installer idea ${stamp}`,
      content: "This idea proves the installed application writes only to the selected workbench.",
    });
    const project = await request(state.info.backendOrigin, "POST", "/api/projects", {
      title: `Installer paper ${stamp}`,
      idea: "Turn the installer smoke idea into a reproducible paper.",
      template_id: "generic",
      bilingual: true,
      git_enabled: false,
    });
    const ideaPath = path.resolve(idea.resource.path);
    const projectId = project.project.id;
    assert(ideaPath.toLowerCase().startsWith(path.resolve(managed, "library", "ideas").toLowerCase()), "Idea escaped library/ideas");
    assert(await exists(path.join(managed, "papercreator.db")), "installed backend did not create the workbench database");
    assert(await exists(path.join(managed, "projects")), "installed backend did not create the projects directory");
    const preservationMarker = path.join(managed, "installer-smoke-preserve.txt");
    await fs.writeFile(preservationMarker, "must survive upgrade and uninstall\n", "utf8");

    await closeApplication(state);

    process.stdout.write("Running same-path silent upgrade\n");
    await run(installer, ["/S", `/D=${installDirectory}`]);
    assert(await exists(executable), "same-path upgrade removed the application executable");
    Object.assign(state, await launchInstalled(executable, workbench));
    const projects = await request(state.info.backendOrigin, "GET", "/api/projects");
    const ideas = await request(state.info.backendOrigin, "GET", "/api/workbench/resources?kind=idea");
    assert(projects.items.some((entry) => entry.id === projectId), "project did not survive same-path upgrade");
    assert(ideas.items.some((entry) => entry.id === idea.resource.id), "Idea did not survive same-path upgrade");
    assert(await exists(preservationMarker), "workbench marker did not survive same-path upgrade");
    await closeApplication(state);

    const uninstaller = await findUninstaller(installDirectory);
    process.stdout.write(`Uninstalling with ${path.basename(uninstaller)}\n`);
    await run(uninstaller, ["/S"]);
    uninstalled = true;
    await poll(async () => !(await exists(executable)), "installed executable removal", 30_000);
    await poll(
      async () => !(await paperCreatorRegistration()),
      "per-user uninstall registration removal",
      30_000,
    );
    assert(await exists(managed), "uninstaller deleted the selected .papercreator directory");
    assert(await exists(preservationMarker), "uninstaller deleted persistent workbench data");
    assert(await exists(ideaPath), "uninstaller deleted the managed Idea");

    process.stdout.write("Installer smoke passed: install → packaged app → data → upgrade → restore → uninstall preserve\n");
  } finally {
    await closeApplication(state);
    if (installed && !uninstalled && await exists(installDirectory)) {
      const uninstaller = await findUninstaller(installDirectory).catch(() => "");
      if (uninstaller) await run(uninstaller, ["/S"]).catch(() => {});
    }
    await fs.rm(safeTemporaryRoot(root), { recursive: true, force: true, maxRetries: 5, retryDelay: 250 });
  }
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error.message}\n`);
  process.exitCode = 1;
});
