/**
 * Electron main process.
 *
 * Owns the Python backend's lifecycle. The desktop app is a single window over a
 * local HTTP service, so the two must start and stop together: a stranded
 * uvicorn process holding port 8765 is the most likely way for the app to fail
 * to start a second time.
 *
 * Startup sequence:
 *   1. packaged: locate the bundled backend executable; dev: find Python
 *   2. spawn the backend, capture its output into a ring buffer for the Output panel
 *   3. poll /api/system/health until it answers
 *   4. load the UI (Vite dev server in development, bundled files otherwise)
 *
 * Shutdown kills the backend explicitly, including on crash and on Windows
 * where SIGTERM is not delivered the way it is on POSIX.
 */

const { app, BrowserWindow, dialog, ipcMain, shell, Menu } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const crypto = require("node:crypto");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { gzipSync, gunzipSync } = require("node:zlib");

const IS_DEV = !app.isPackaged;
// E2E launches the source Electron entry against the already-built renderer.
// It still uses the real development backend, but does not require a separate
// Vite server or open detached DevTools windows in CI.
const IS_E2E = process.env.PC_E2E === "1";
// Windows CI/agent sessions often have no usable GPU runtime or lose the GPU
// process after a previous Electron run. The renderer features exercised by
// E2E do not need acceleration; disabling it before app.ready prevents Chromium
// from terminating the whole application after repeated GPU process crashes.
if (IS_E2E) {
  app.disableHardwareAcceleration();
  app.commandLine.appendSwitch("disable-gpu");
  app.commandLine.appendSwitch("disable-gpu-compositing");
  app.commandLine.appendSwitch("disable-gpu-sandbox");
  app.commandLine.appendSwitch("in-process-gpu");
}
const DEV_URL = "http://localhost:5173";
const BACKEND_HOST = "127.0.0.1";
const BACKEND_PORT = Number(process.env.PC_PORT || 8765);
const BACKEND_ORIGIN = `http://${BACKEND_HOST}:${BACKEND_PORT}`;
// This capability is inherited only by the owned backend process.  It is never
// exposed through preload or renderer state and changes on every desktop run.
const BACKEND_SHUTDOWN_TOKEN = crypto.randomBytes(32).toString("hex");

// The launcher keeps one tiny locator in Windows AppData so it can find the
// last selected workbench before Chromium starts. All substantive app state -
// including Chromium local storage - is redirected into that workbench's
// .papercreator/electron directory before app.ready.
const GLOBAL_LAUNCHER_DIR = app.getPath("userData");
const WORKBENCH_POINTER = path.join(GLOBAL_LAUNCHER_DIR, "workbench-location.json");
const MANAGED_DIRNAME = ".papercreator";

/** @type {string | null} */
let workbenchRoot = null;

function normaliseWorkbenchRoot(candidate) {
  if (typeof candidate !== "string" || !candidate.trim()) return null;
  let root = path.resolve(candidate.trim());
  // Selecting the hidden directory itself is a common first-run mistake. Treat
  // its parent as the workbench rather than nesting .papercreator inside it.
  if (path.basename(root).toLowerCase() === MANAGED_DIRNAME) root = path.dirname(root);
  try {
    if (!fs.statSync(root).isDirectory()) return null;
  } catch {
    return null;
  }
  return root;
}

function readRememberedWorkbench() {
  try {
    const data = JSON.parse(fs.readFileSync(WORKBENCH_POINTER, "utf8"));
    return normaliseWorkbenchRoot(data?.path);
  } catch {
    return null;
  }
}

function rememberWorkbench(root) {
  fs.mkdirSync(GLOBAL_LAUNCHER_DIR, { recursive: true });
  const temporary = `${WORKBENCH_POINTER}.tmp`;
  fs.writeFileSync(
    temporary,
    JSON.stringify({ format: "papercreator-workbench-location", path: root }, null, 2),
    "utf8",
  );
  fs.rmSync(WORKBENCH_POINTER, { force: true });
  fs.renameSync(temporary, WORKBENCH_POINTER);
}

function initialiseWorkbenchRoot(root) {
  const managed = path.join(root, MANAGED_DIRNAME);
  fs.mkdirSync(managed, { recursive: true });
  // Fail before launching the backend if the selected drive is read-only.
  const probe = path.join(managed, `.write-probe-${process.pid}`);
  fs.writeFileSync(probe, "PaperCreator write test\n", "utf8");
  fs.unlinkSync(probe);
  return managed;
}

function configureElectronStorage(root) {
  const electronData = path.join(root, MANAGED_DIRNAME, "electron");
  const sessionData = path.join(electronData, "session");
  fs.mkdirSync(electronData, { recursive: true });
  fs.mkdirSync(sessionData, { recursive: true });
  app.setPath("userData", electronData);
  app.setPath("sessionData", sessionData);
}

function savedDesktopLocale() {
  if (!workbenchRoot) return "zh-CN";
  try {
    const value = JSON.parse(
      fs.readFileSync(path.join(workbenchRoot, MANAGED_DIRNAME, "config", "settings.json"), "utf8"),
    )?.ui?.locale;
    return value === "en-US" ? "en-US" : "zh-CN";
  } catch {
    return "zh-CN";
  }
}

function desktopText(zh, en) {
  return savedDesktopLocale() === "zh-CN" ? zh : en;
}

// Resolve the workbench early enough to keep Electron's own browser state in
// it. Development deliberately uses the repository as its workbench; packaged
// builds prefer an explicit environment override, then the remembered choice.
workbenchRoot = normaliseWorkbenchRoot(process.env.PAPERCREATOR_WORKBENCH || "");
if (!workbenchRoot && IS_DEV) {
  workbenchRoot = normaliseWorkbenchRoot(path.resolve(__dirname, "..", "..", ".."));
}
if (!workbenchRoot) workbenchRoot = readRememberedWorkbench();
if (workbenchRoot) {
  initialiseWorkbenchRoot(workbenchRoot);
  configureElectronStorage(workbenchRoot);
}

/** @type {BrowserWindow | null} */
let mainWindow = null;
/** @type {Electron.Menu | null} */
let applicationMenu = null;
/** @type {import("node:child_process").ChildProcess | null} */
let backend = null;
let backendExited = false;
let backendReady = false;
let backendStopDiagnostic = "";
let allowQuitAfterBackendExit = false;
let pendingAppQuit = null;
let destroyingRendererForQuit = false;
let rendererQuitPreparation = null;
let rendererLifecycleReady = false;
/** Ring buffer of backend output lines, shown in the app's Output panel. */
const backendLog = [];
const BACKEND_LOG_MAX = 2000;

function recordBackendLine(line, stream) {
  const text = String(line).replace(/\s+$/, "");
  if (!text) return;
  backendLog.push({ ts: Date.now(), stream, text });
  if (backendLog.length > BACKEND_LOG_MAX) backendLog.shift();
  if (workbenchRoot) {
    try {
      const logsDir = path.join(workbenchRoot, MANAGED_DIRNAME, "logs");
      fs.mkdirSync(logsDir, { recursive: true });
      fs.appendFileSync(
        path.join(logsDir, "desktop.log"),
        `${new Date().toISOString()} [${stream}] ${text}\n`,
        "utf8",
      );
    } catch {
      // The in-memory ring remains available even if the selected drive becomes
      // read-only after startup. Never recurse by logging this write failure.
    }
  }
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("backend:log", { stream, text });
  }
  if (IS_DEV) process.stdout.write(`[backend] ${text}\n`);
}

/**
 * Locate the backend source directory used in development.
 */
function backendDir() {
  const candidates = [path.resolve(__dirname, "..", "..", "..", "backend")];
  return candidates.find((p) => fs.existsSync(path.join(p, "papercreator"))) || null;
}

/** The PyInstaller runtime copied by electron-builder's extraResources. */
function packagedBackendExecutable() {
  const name = process.platform === "win32"
    ? "papercreator-backend.exe"
    : "papercreator-backend";
  return path.join(process.resourcesPath || "", "backend", name);
}

function isFile(target) {
  try {
    return fs.statSync(target).isFile();
  } catch {
    return false;
  }
}

/**
 * Pick a Python interpreter.
 *
 * Packaged builds do not call this: they use the PyInstaller runtime above.
 * Each development candidate is probed with
 * `-c "import sys"` because a `python` on PATH that is really the Windows Store
 * stub exits non-zero and would otherwise look available.
 */
function findPython() {
  const dir = backendDir();
  const candidates = [];
  if (process.env.PC_PYTHON) candidates.push(process.env.PC_PYTHON);
  if (dir) {
    candidates.push(
      path.join(dir, ".venv", "Scripts", "python.exe"),
      path.join(dir, ".venv", "bin", "python"),
    );
  }
  candidates.push(
    path.resolve(__dirname, "..", "..", "..", ".venv", "Scripts", "python.exe"),
    path.resolve(__dirname, "..", "..", "..", ".venv", "bin", "python"),
  );
  if (process.platform === "win32") candidates.push("python.exe", "py");
  candidates.push("python3", "python");

  for (const candidate of candidates) {
    try {
      const probe = spawnSync(candidate, ["-c", "import sys; print(sys.version)"], {
        encoding: "utf8",
        timeout: 15000,
        windowsHide: true,
      });
      if (probe.status === 0 && String(probe.stdout || "").trim()) {
        return { path: candidate, version: String(probe.stdout).trim().split(" ")[0] };
      }
    } catch {
      /* try the next candidate */
    }
  }
  return null;
}

function healthCheck(timeoutMs = 1500) {
  return new Promise((resolve) => {
    const request = http.get(
      `${BACKEND_ORIGIN}/api/system/health`,
      { timeout: timeoutMs },
      (response) => {
        response.resume();
        resolve(response.statusCode === 200);
      },
    );
    request.on("error", () => resolve(false));
    request.on("timeout", () => {
      request.destroy();
      resolve(false);
    });
  });
}

async function waitForBackend(timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (backendExited) return false;
    if (await healthCheck()) return true;
    await new Promise((r) => setTimeout(r, 400));
  }
  return false;
}

async function startBackend() {
  // An already-running instance (developer ran `npm run backend` separately) is
  // reused rather than fought over the port.
  if (await healthCheck()) {
    backendReady = true;
    recordBackendLine("reusing the backend already listening on this port", "info");
    return { reused: true };
  }

  let command;
  let args;
  let cwd;
  let diagnostic;
  let runtimeLabel;

  if (IS_DEV) {
    const dir = backendDir();
    if (!dir) {
      return { error: "Could not find the backend source (expected backend/papercreator)." };
    }
    const python = findPython();
    if (!python) {
      return {
        error:
          "No usable Python interpreter was found. Run `npm run setup`, or set " +
          "PC_PYTHON to the project's interpreter.",
      };
    }
    command = python.path;
    args = ["-m", "papercreator", "--port", String(BACKEND_PORT)];
    cwd = dir;
    diagnostic = `cd ${dir}\n  ${python.path} -m papercreator --check`;
    runtimeLabel = `${python.path} (Python ${python.version})`;
  } else {
    const executable = packagedBackendExecutable();
    if (!isFile(executable)) {
      recordBackendLine(`bundled backend is missing at ${executable}`, "stderr");
      return {
        error:
          "The bundled PaperCreator backend is missing. The installation is " +
          `incomplete or damaged (expected ${executable}). Reinstall the app.`,
      };
    }
    command = executable;
    args = ["--port", String(BACKEND_PORT)];
    cwd = path.dirname(executable);
    diagnostic = `${executable} --check`;
    runtimeLabel = executable;
  }
  recordBackendLine(`starting backend with ${runtimeLabel}`, "info");

  backendExited = false;
  backendReady = false;
  const child = spawn(command, args, {
    cwd,
    windowsHide: true,
    env: {
      ...process.env,
      PAPERCREATOR_WORKBENCH: workbenchRoot,
      PC_DESKTOP_SHUTDOWN_TOKEN: BACKEND_SHUTDOWN_TOKEN,
      PYTHONUNBUFFERED: "1",
      // Without this the child inherits the parent's encoding, and Chinese log
      // lines arrive as mojibake on Windows.
      PYTHONIOENCODING: "utf-8",
      ...(IS_DEV ? { PYTHONPATH: cwd } : {}),
    },
  });
  backend = child;

  child.stdout.on("data", (chunk) =>
    String(chunk).split(/\r?\n/).forEach((l) => recordBackendLine(l, "stdout")),
  );
  child.stderr.on("data", (chunk) =>
    String(chunk).split(/\r?\n/).forEach((l) => recordBackendLine(l, "stderr")),
  );
  child.on("exit", (code, signal) => {
    const isCurrentBackend = backend === child;
    if (isCurrentBackend) {
      backendExited = true;
      backendReady = false;
      backend = null;
    }
    recordBackendLine(`backend exited (code ${code}, signal ${signal})`, "info");
    if (isCurrentBackend && mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("backend:exit", { code, signal });
    }
  });
  child.on("error", (error) => {
    if (backend === child) {
      backendExited = true;
      backendReady = false;
      backend = null;
    }
    recordBackendLine(`backend could not start: ${error.message}`, "stderr");
  });

  const ready = await waitForBackend();
  if (!ready) {
    backendReady = false;
    const tail = backendLog.slice(-25).map((l) => l.text).join("\n");
    return {
      error:
        `The backend did not become ready on ${BACKEND_ORIGIN}.\n\n` +
        `Last output:\n${tail}\n\n` +
        `Try running the backend diagnostic directly:\n  ${diagnostic}`,
    };
  }
  backendReady = true;
  return { started: true, runtime: command };
}

function stopBackend() {
  if (!backend || backendExited) return null;
  const child = backend;
  // Detach global ownership before terminating. Its asynchronous exit event
  // must not mark a replacement process as exited during backend:restart.
  backend = null;
  backendExited = true;
  backendReady = false;
  backendStopDiagnostic = "";
  recordBackendLine("stopping backend", "info");
  try {
    if (process.platform === "win32") {
      // A Windows venv launcher can sit between Electron and the real Python
      // process, so neither stdin nor ChildProcess.kill reliably reaches
      // Uvicorn.  Use a per-launch authenticated loopback capability instead.
      requestGracefulBackendShutdown();
    } else {
      child.kill("SIGTERM");
      setTimeout(() => {
        if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
      }, 4000);
    }
  } catch (error) {
    recordBackendLine(`could not stop backend: ${error.message}`, "stderr");
  }
  return child;
}

function requestGracefulBackendShutdown() {
  const request = http.request(
    `${BACKEND_ORIGIN}/api/system/shutdown`,
    {
      method: "POST",
      headers: {
        "X-PaperCreator-Shutdown": BACKEND_SHUTDOWN_TOKEN,
        "Content-Length": "0",
      },
      timeout: 2000,
    },
    (response) => {
      response.resume();
      response.on("end", () => {
        if (response.statusCode === 200) {
          recordBackendLine("requested graceful backend shutdown", "info");
        } else {
          recordBackendLine(
            `backend rejected graceful shutdown (HTTP ${response.statusCode ?? "unknown"})`,
            "stderr",
          );
        }
      });
    },
  );
  request.on("timeout", () => request.destroy(new Error("shutdown request timed out")));
  request.on("error", (error) => {
    backendStopDiagnostic = `the backend shutdown request failed: ${error.message}`;
    recordBackendLine(backendStopDiagnostic, "stderr");
  });
  request.end();
}

/**
 * Ask the owned Python process to stop and keep Electron alive until Windows
 * reports that the process handle has actually exited.  ChildProcess.kill()
 * only requests termination; letting the Electron parent disappear
 * immediately can leave SQLite/WAL handles visible for several seconds and
 * makes switching or removing a workbench unreliable.
 */
async function stopBackendAndWait(timeoutMs = 8000) {
  const child = stopBackend();
  if (!child || child.exitCode !== null || child.signalCode !== null) return true;

  return new Promise((resolve) => {
    let settled = false;
    let forceTimer = null;
    let timeoutTimer = null;
    const finish = (exited) => {
      if (settled) return;
      settled = true;
      if (forceTimer) clearTimeout(forceTimer);
      if (timeoutTimer) clearTimeout(timeoutTimer);
      child.removeListener("exit", onExit);
      child.removeListener("error", onError);
      resolve(exited);
    };
    const onExit = () => finish(true);
    const onError = () => finish(false);
    child.once("exit", onExit);
    child.once("error", onError);

    // POSIX SIGTERM allows graceful cleanup.  On Windows Node already maps the
    // first kill to TerminateProcess; this fallback is harmless and bounded.
    forceTimer = setTimeout(() => {
      if (child.exitCode === null && child.signalCode === null) {
        try {
          if (process.platform === "win32" && child.pid) {
            // Fallback only: terminate the exact owned process tree so a venv
            // launcher cannot orphan its real Python child.  Normal shutdown
            // uses the authenticated endpoint above and never reaches this.
            const forced = spawnSync(
              "taskkill",
              ["/PID", String(child.pid), "/T", "/F"],
              { windowsHide: true, encoding: "utf8" },
            );
            if (forced.status !== 0 && child.exitCode === null) child.kill("SIGKILL");
          } else {
            child.kill("SIGKILL");
          }
        } catch (error) {
          recordBackendLine(`could not force-stop backend: ${error.message}`, "stderr");
        }
      }
    }, Math.max(250, Math.min(6000, timeoutMs - 1000)));
    timeoutTimer = setTimeout(() => {
      const exited = child.exitCode !== null || child.signalCode !== null;
      if (!exited) recordBackendLine("timed out waiting for backend process exit", "stderr");
      finish(exited);
    }, timeoutMs);
  });
}

function createWindow() {
  const customTitleBar = process.platform === "win32";
  rendererLifecycleReady = false;
  mainWindow = new BrowserWindow({
    width: 1680,
    height: 1000,
    minWidth: 1100,
    minHeight: 700,
    icon: path.resolve(__dirname, "..", "assets", "brand", "icon.png"),
    backgroundColor: "#1e1e1e",
    title: "PaperCreator",
    // On Windows the renderer owns one VS Code-style row (brand, menus, project
    // switcher, app actions). titleBarOverlay retains native minimise/maximise/
    // close behaviour, snap layouts and accessibility on the right.
    titleBarStyle:
      process.platform === "darwin" ? "hiddenInset" : customTitleBar ? "hidden" : "default",
    ...(customTitleBar
      ? {
          titleBarOverlay: {
            color: "#3c3c3c",
            symbolColor: "#f0f0f0",
            height: 35,
          },
        }
      : {}),
    autoHideMenuBar: customTitleBar,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      spellcheck: true,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow?.show());
  mainWindow.on("close", (event) => {
    if (allowQuitAfterBackendExit || destroyingRendererForQuit) return;
    // Native window close occurs before app.before-quit. Redirect it through
    // the coordinated path so renderer-owned drafts can be saved first.
    event.preventDefault();
    app.quit();
  });
  mainWindow.on("closed", () => {
    rendererLifecycleReady = false;
    mainWindow = null;
  });

  // External links open in the real browser, never inside the app window.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url)) shell.openExternal(url);
    return { action: "deny" };
  });

  if (IS_DEV && !IS_E2E) {
    mainWindow.loadURL(DEV_URL);
    mainWindow.webContents.openDevTools({ mode: "detach" });
  } else {
    mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }
}

async function openLogFolder() {
  if (!workbenchRoot) return "workbench not selected";
  const logsDirectory = path.join(workbenchRoot, MANAGED_DIRNAME, "logs");
  fs.mkdirSync(logsDirectory, { recursive: true });
  if (IS_E2E) return "";
  return shell.openPath(logsDirectory);
}

function buildMenu() {
  const isMac = process.platform === "darwin";
  const template = [
    ...(isMac ? [{ role: "appMenu" }] : []),
    {
      id: "File",
      label: desktopText("文件", "File"),
      submenu: [
        {
          label: desktopText("新建项目", "New Project"),
          accelerator: "CmdOrCtrl+N",
          click: () => mainWindow?.webContents.send("menu", "project.new"),
        },
        {
          label: desktopText("打开其他工作台文件夹…", "Open Workbench Folder..."),
          click: () => void chooseAnotherWorkbench(),
        },
        {
          label: desktopText("保存", "Save"),
          accelerator: "CmdOrCtrl+S",
          click: () => mainWindow?.webContents.send("menu", "document.save"),
        },
        {
          label: desktopText("提交本地版本", "Commit Local Version"),
          accelerator: "CmdOrCtrl+Shift+S",
          click: () => mainWindow?.webContents.send("menu", "version.commit"),
        },
        { type: "separator" },
        {
          label: desktopText("导出…", "Export..."),
          accelerator: "CmdOrCtrl+E",
          click: () => mainWindow?.webContents.send("menu", "export.open"),
        },
        { type: "separator" },
        isMac ? { role: "close" } : { role: "quit" },
      ],
    },
    { id: "Edit", label: desktopText("编辑", "Edit"), submenu: [
      { role: "undo" }, { role: "redo" }, { type: "separator" },
      { role: "cut" }, { role: "copy" }, { role: "paste" },
      { role: "selectAll" },
    ] },
    {
      id: "View",
      label: desktopText("视图", "View"),
      submenu: [
        {
          label: desktopText("命令面板", "Command Palette"),
          accelerator: "CmdOrCtrl+Shift+P",
          click: () => mainWindow?.webContents.send("menu", "palette.open"),
        },
        { type: "separator" },
        { role: "reload" }, { role: "forceReload" },
        { role: "toggleDevTools" },
        { type: "separator" },
        { role: "resetZoom" }, { role: "zoomIn" }, { role: "zoomOut" },
        { type: "separator" },
        { role: "togglefullscreen" },
      ],
    },
    {
      id: "Help",
      label: desktopText("帮助", "Help"),
      submenu: [
        {
          label: desktopText("快速开始", "Quick Start"),
          click: () => mainWindow?.webContents.send("menu", "help.quickStart"),
        },
        { type: "separator" },
        {
          label: desktopText("后端诊断", "Backend Diagnostics"),
          click: () => mainWindow?.webContents.send("menu", "system.diagnostics"),
        },
        {
          label: desktopText("打开日志目录", "Open Log Folder"),
          click: () => void openLogFolder(),
        },
        {
          label: desktopText("API 文档", "API Documentation"),
          click: () => shell.openExternal(`${BACKEND_ORIGIN}/api/docs`),
        },
      ],
    },
  ];
  applicationMenu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(applicationMenu);
  // Accelerators stay registered through the application menu, while Windows
  // renders the same menu model from the custom one-row title bar.
  if (process.platform === "win32") mainWindow?.setMenuBarVisibility(false);
}

// ------------------------------------------------------------------- IPC

ipcMain.handle("app:info", () => ({
  version: app.getVersion(),
  isDev: IS_DEV,
  platform: process.platform,
  backendOrigin: BACKEND_ORIGIN,
  backendRunning: backendReady,
  workbench: workbenchRoot || "",
  managedDirectory: workbenchRoot
    ? path.join(workbenchRoot, MANAGED_DIRNAME)
    : "",
  backendExecutable: IS_DEV ? "" : packagedBackendExecutable(),
  bundledBackendExists: IS_DEV || isFile(packagedBackendExecutable()),
}));

ipcMain.on("lifecycle:prepared", (event, payload = {}) => {
  if (
    !rendererQuitPreparation ||
    !mainWindow ||
    mainWindow.isDestroyed() ||
    event.sender !== mainWindow.webContents ||
    payload.id !== rendererQuitPreparation.id
  ) {
    return;
  }
  rendererQuitPreparation.finish({
    ok: payload.ok === true,
    error: String(payload.error ?? ""),
  });
});

ipcMain.on("lifecycle:ready", (event) => {
  if (mainWindow && !mainWindow.isDestroyed() && event.sender === mainWindow.webContents) {
    rendererLifecycleReady = true;
  }
});

function prepareRendererForQuit(timeoutMs = 15000) {
  if (!mainWindow || mainWindow.isDestroyed()) return Promise.resolve({ ok: true, error: "" });
  // Before React mounts there cannot be renderer-owned manuscript drafts.
  if (!rendererLifecycleReady) return Promise.resolve({ ok: true, error: "" });
  if (rendererQuitPreparation) return rendererQuitPreparation.promise;

  const id = crypto.randomUUID();
  let resolvePreparation;
  let timer;
  const promise = new Promise((resolve) => {
    resolvePreparation = resolve;
  });
  const finish = (result) => {
    if (!rendererQuitPreparation || rendererQuitPreparation.id !== id) return;
    clearTimeout(timer);
    rendererQuitPreparation = null;
    resolvePreparation(result);
  };
  rendererQuitPreparation = { id, promise, finish };
  timer = setTimeout(
    () => finish({ ok: false, error: "timed out while saving renderer drafts" }),
    timeoutMs,
  );
  mainWindow.webContents.send("lifecycle:prepare-quit", { id });
  return promise;
}

ipcMain.handle("menu:popup", (event, menuId, position = {}) => {
  const owner = BrowserWindow.fromWebContents(event.sender);
  if (!owner || owner !== mainWindow || !applicationMenu) return { opened: false };
  const allowed = new Set(["File", "Edit", "View", "Help"]);
  if (typeof menuId !== "string" || !allowed.has(menuId)) return { opened: false };
  const item = applicationMenu.items.find((candidate) => candidate.id === menuId);
  if (!item?.submenu) return { opened: false };
  const bounds = owner.getContentBounds();
  const x = Math.max(0, Math.min(Math.round(Number(position.x) || 0), bounds.width - 1));
  const y = Math.max(0, Math.min(Math.round(Number(position.y) || 35), bounds.height - 1));
  item.submenu.popup({ window: owner, x, y });
  return { opened: true, menu: menuId };
});

ipcMain.handle("workbench:info", () => ({
  path: workbenchRoot || "",
  managedDirectory: workbenchRoot
    ? path.join(workbenchRoot, MANAGED_DIRNAME)
    : "",
}));

ipcMain.handle("workbench:choose", () => chooseAnotherWorkbench());

ipcMain.handle("backend:log", () => backendLog.slice(-500));

ipcMain.handle("app:openLogs", () => openLogFolder());

ipcMain.handle("backend:restart", async () => {
  await stopBackendAndWait();
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline && await healthCheck(500)) {
    await new Promise((r) => setTimeout(r, 250));
  }
  if (await healthCheck(500)) {
    return {
      error:
        "The previous backend did not stop, so PaperCreator refused to start a " +
        `second process on the same port.${backendStopDiagnostic ? ` ${backendStopDiagnostic}` : ""}`,
    };
  }
  return startBackend();
});

ipcMain.handle("dialog:openDirectory", async (_event, options = {}) => {
  if (IS_E2E && process.env.PC_E2E_OPEN_DIRECTORY) {
    const candidate = path.resolve(process.env.PC_E2E_OPEN_DIRECTORY);
    const insideWorkbench = Boolean(
      workbenchRoot &&
      (candidate === workbenchRoot || candidate.startsWith(`${workbenchRoot}${path.sep}`)),
    );
    let isDirectory = false;
    try {
      isDirectory = fs.statSync(candidate).isDirectory();
    } catch {
      isDirectory = false;
    }
    if (!insideWorkbench || !isDirectory) {
      throw new Error(
        "PC_E2E_OPEN_DIRECTORY must name a directory inside the isolated workbench",
      );
    }
    return candidate;
  }
  const result = await dialog.showOpenDialog(mainWindow, {
    title: options.title || "Choose a folder",
    defaultPath: options.defaultPath || undefined,
    // Electron's option is `properties`; it must be set last so a caller cannot
    // accidentally turn this into a file picker.
    properties: ["openDirectory", "createDirectory"],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("dialog:openFile", async (_event, filters = []) => {
  if (IS_E2E && process.env.PC_E2E_OPEN_FILE) {
    const candidate = path.resolve(process.env.PC_E2E_OPEN_FILE);
    const insideWorkbench = Boolean(
      workbenchRoot &&
      (candidate === workbenchRoot || candidate.startsWith(`${workbenchRoot}${path.sep}`)),
    );
    if (!insideWorkbench || !isFile(candidate)) {
      throw new Error("PC_E2E_OPEN_FILE must name a file inside the isolated workbench");
    }
    return candidate;
  }
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ["openFile"],
    filters: filters.length
      ? filters
      : [
          { name: "Bibliography", extensions: ["bib", "ris", "csv", "json"] },
          { name: "All files", extensions: ["*"] },
        ],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle("dialog:saveJson", async (_event, options = {}) => {
  if (!options || typeof options !== "object" || options.data === undefined) {
    throw new Error("JSON export data is required");
  }
  const serialized = `${JSON.stringify(options.data, null, 2)}\n`;
  // IPC serialises the payload in memory, so reject pathological exports before
  // another copy is allocated by the filesystem layer.
  const bytes = Buffer.byteLength(serialized, "utf8");
  if (bytes > 256 * 1024 * 1024) {
    throw new Error("JSON export exceeds the 256 MiB desktop save limit");
  }
  const requestedName = path.basename(String(options.suggestedName || "assistant-conversations.json"));
  const safeName = (requestedName.replace(/[<>:\"/\\|?*\x00-\x1f]/g, "-").trim() || "assistant-conversations.json")
    .replace(/\.+$/, "");
  const defaultName = safeName.toLowerCase().endsWith(".json") ? safeName : `${safeName}.json`;
  let target = "";
  if (IS_E2E && process.env.PC_E2E_SAVE_JSON) {
    const candidate = path.resolve(process.env.PC_E2E_SAVE_JSON);
    const insideWorkbench = Boolean(
      workbenchRoot &&
      (candidate === workbenchRoot || candidate.startsWith(`${workbenchRoot}${path.sep}`)),
    );
    if (!insideWorkbench || path.extname(candidate).toLowerCase() !== ".json") {
      throw new Error("PC_E2E_SAVE_JSON must name a .json file inside the isolated workbench");
    }
    target = candidate;
  } else {
    const result = await dialog.showSaveDialog(mainWindow, {
      title: desktopText("导出 AI 对话", "Export AI conversations"),
      defaultPath: path.join(app.getPath("documents"), defaultName),
      filters: [{ name: "JSON", extensions: ["json"] }],
      properties: ["showOverwriteConfirmation", "createDirectory"],
    });
    if (result.canceled || !result.filePath) return { canceled: true };
    target = result.filePath.toLowerCase().endsWith(".json")
      ? result.filePath
      : `${result.filePath}.json`;
  }
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const handle = fs.openSync(target, "w", 0o600);
  try {
    fs.writeFileSync(handle, serialized, "utf8");
    fs.fsyncSync(handle);
  } finally {
    fs.closeSync(handle);
  }
  return { canceled: false, path: target, bytes };
});

ipcMain.handle("dialog:saveAssistantArchive", async (_event, options = {}) => {
  if (!options || typeof options !== "object" || options.data === undefined) {
    throw new Error("assistant archive data is required");
  }
  const serialized = `${JSON.stringify(options.data, null, 2)}\n`;
  const source = Buffer.from(serialized, "utf8");
  if (source.byteLength > 256 * 1024 * 1024) {
    throw new Error("assistant archive exceeds the 256 MiB uncompressed limit");
  }
  const compressed = options.compressed !== false;
  const output = compressed ? gzipSync(source, { level: 9 }) : source;
  const requestedName = path.basename(String(
    options.suggestedName || `assistant-conversations.json${compressed ? ".gz" : ""}`,
  ));
  const safeName = (
    requestedName.replace(/[<>:"/\\|?*\x00-\x1f]/g, "-").trim() ||
    `assistant-conversations.json${compressed ? ".gz" : ""}`
  ).replace(/\.+$/, "");
  const suffix = compressed ? ".json.gz" : ".json";
  const defaultName = safeName.toLowerCase().endsWith(suffix) ? safeName : `${safeName}${suffix}`;
  let target = "";
  if (IS_E2E && process.env.PC_E2E_SAVE_ASSISTANT_ARCHIVE) {
    const candidate = path.resolve(process.env.PC_E2E_SAVE_ASSISTANT_ARCHIVE);
    const insideWorkbench = Boolean(
      workbenchRoot && candidate.startsWith(`${workbenchRoot}${path.sep}`),
    );
    if (!insideWorkbench || !candidate.toLowerCase().endsWith(suffix)) {
      throw new Error(`PC_E2E_SAVE_ASSISTANT_ARCHIVE must name a ${suffix} file inside the isolated workbench`);
    }
    target = candidate;
  } else {
    const result = await dialog.showSaveDialog(mainWindow, {
      title: desktopText("导出 AI 对话归档", "Export AI conversation archive"),
      defaultPath: path.join(app.getPath("documents"), defaultName),
      filters: [{ name: compressed ? "Compressed JSON" : "JSON", extensions: [compressed ? "gz" : "json"] }],
      properties: ["showOverwriteConfirmation", "createDirectory"],
    });
    if (result.canceled || !result.filePath) return { canceled: true };
    target = result.filePath.toLowerCase().endsWith(suffix)
      ? result.filePath
      : `${result.filePath}${suffix}`;
  }
  fs.mkdirSync(path.dirname(target), { recursive: true });
  const handle = fs.openSync(target, "w", 0o600);
  try {
    fs.writeFileSync(handle, output);
    fs.fsyncSync(handle);
  } finally {
    fs.closeSync(handle);
  }
  return {
    canceled: false,
    path: target,
    bytes: output.byteLength,
    uncompressedBytes: source.byteLength,
    compressed,
  };
});

ipcMain.handle("dialog:openAssistantArchive", async () => {
  let target = "";
  if (IS_E2E && process.env.PC_E2E_OPEN_ASSISTANT_ARCHIVE) {
    const candidate = path.resolve(process.env.PC_E2E_OPEN_ASSISTANT_ARCHIVE);
    const insideWorkbench = Boolean(
      workbenchRoot && candidate.startsWith(`${workbenchRoot}${path.sep}`),
    );
    if (!insideWorkbench || !isFile(candidate)) {
      throw new Error("PC_E2E_OPEN_ASSISTANT_ARCHIVE must name a file inside the isolated workbench");
    }
    target = candidate;
  } else {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: desktopText("导入 AI 对话归档", "Import AI conversation archive"),
      properties: ["openFile"],
      filters: [
        { name: "PaperCreator assistant archives", extensions: ["json", "gz"] },
        { name: "All files", extensions: ["*"] },
      ],
    });
    if (result.canceled || !result.filePaths[0]) return { canceled: true };
    target = result.filePaths[0];
  }
  const lower = target.toLowerCase();
  if (!lower.endsWith(".json") && !lower.endsWith(".json.gz")) {
    throw new Error("assistant archive must use .json or .json.gz");
  }
  const stat = fs.statSync(target);
  if (!stat.isFile() || stat.size > 256 * 1024 * 1024) {
    throw new Error("assistant archive is not a file or exceeds the 256 MiB compressed limit");
  }
  const raw = fs.readFileSync(target);
  const compressed = lower.endsWith(".gz");
  const decoded = compressed
    ? gunzipSync(raw, { maxOutputLength: 256 * 1024 * 1024 })
    : raw;
  if (decoded.byteLength > 256 * 1024 * 1024) {
    throw new Error("assistant archive exceeds the 256 MiB uncompressed limit");
  }
  let data;
  try {
    data = JSON.parse(decoded.toString("utf8").replace(/^\uFEFF/, ""));
  } catch (error) {
    throw new Error(`assistant archive is not valid JSON: ${error.message}`);
  }
  return {
    canceled: false,
    path: target,
    data,
    bytes: raw.byteLength,
    uncompressedBytes: decoded.byteLength,
    compressed,
  };
});

ipcMain.handle("shell:showItem", (_event, target) => {
  // E2E verifies that exports are written through the real backend, but must
  // not open Explorer on the developer/CI desktop as a test side effect.
  if (IS_E2E) return { skipped: true, reason: "e2e" };
  if (typeof target === "string" && target) shell.showItemInFolder(target);
});

ipcMain.handle("shell:openPath", async (_event, target) => {
  if (typeof target !== "string" || !target) return "no path given";
  if (IS_E2E) return "";
  return shell.openPath(target);
});

ipcMain.handle("shell:openExternal", async (_event, url) => {
  if (IS_E2E) return;
  if (/^https?:\/\//.test(String(url))) await shell.openExternal(url);
});

async function selectWorkbenchDirectory() {
  const result = await dialog.showOpenDialog(mainWindow, {
    title: desktopText("选择 PaperCreator 工作台文件夹", "Choose a PaperCreator workbench folder"),
    buttonLabel: desktopText("使用此文件夹", "Use this folder"),
    defaultPath: workbenchRoot || undefined,
    properties: ["openDirectory", "createDirectory"],
  });
  if (result.canceled || !result.filePaths[0]) return null;
  const selected = normaliseWorkbenchRoot(result.filePaths[0]);
  if (!selected) {
    dialog.showErrorBox(
      desktopText("工作台无效", "Invalid workbench"),
      desktopText("请选择一个已经存在的文件夹。", "Choose an existing folder."),
    );
    return null;
  }
  try {
    initialiseWorkbenchRoot(selected);
  } catch (error) {
    dialog.showErrorBox(
      desktopText("PaperCreator 无法使用此文件夹", "PaperCreator cannot use this folder"),
      desktopText(
        `文件夹不可写：\n${selected}\n\n${error.message}`,
        `The folder is not writable:\n${selected}\n\n${error.message}`,
      ),
    );
    return null;
  }
  return selected;
}

async function chooseAnotherWorkbench() {
  const selected = await selectWorkbenchDirectory();
  if (!selected || selected === workbenchRoot) return { changed: false };
  const answer = await dialog.showMessageBox(mainWindow, {
    type: "info",
    title: desktopText("切换 PaperCreator 工作台", "Switch PaperCreator workbench"),
    message: desktopText(
      "PaperCreator 将在所选工作台中重新启动。",
      "PaperCreator will restart in the selected workbench.",
    ),
    detail: desktopText(
      `托管数据将保存在：\n${path.join(selected, MANAGED_DIRNAME)}\n\n当前工作台不会被移动或删除。`,
      `Managed data will live in:\n${path.join(selected, MANAGED_DIRNAME)}\n\nThe current workbench is not moved or deleted.`,
    ),
    buttons: desktopText(["重新启动", "取消"], ["Restart", "Cancel"]),
    defaultId: 0,
    cancelId: 1,
  });
  if (answer.response !== 0) return { changed: false };
  rememberWorkbench(selected);
  app.relaunch();
  app.exit(0);
  return { changed: true };
}

async function ensureWorkbenchSelected() {
  if (workbenchRoot) return true;
  const intro = await dialog.showMessageBox({
    type: "info",
    title: desktopText("欢迎使用 PaperCreator", "Welcome to PaperCreator"),
    message: desktopText(
      "请选择一个文件夹作为 PaperCreator 工作台。",
      "Choose a folder for your PaperCreator workbench.",
    ),
    detail: desktopText(
      "PaperCreator 会在其中创建一个隐藏的 .papercreator 文件夹。项目、想法、论文、代码、索引、设置和日志都保存在里面，整个工作台可以一起备份或迁移。",
      "PaperCreator creates one hidden .papercreator directory inside it. Projects, ideas, papers, code, indexes, settings and logs stay there, so the workbench can be backed up or moved as one unit.",
    ),
    buttons: desktopText(["选择文件夹", "退出"], ["Choose folder", "Exit"]),
    defaultId: 0,
    cancelId: 1,
  });
  if (intro.response !== 0) return false;
  const selected = await selectWorkbenchDirectory();
  if (!selected) return false;
  rememberWorkbench(selected);
  // Storage paths must be set before Chromium creates the first renderer. A
  // clean relaunch is the only reliable first-run transition on Windows.
  app.relaunch();
  app.exit(0);
  return false;
}

// ------------------------------------------------------------- lifecycle

// A second process never opens the same SQLite database.  It becomes an explicit
// launcher action in the existing window: continue with search, or select a
// different workbench and restart cleanly there.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", async () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.show();
      mainWindow.focus();
    }
    if (IS_E2E) {
      mainWindow?.webContents.send("menu", "search.open");
      return;
    }
    const options = {
      type: "question",
      title: desktopText("PaperCreator 已在运行", "PaperCreator is already running"),
      message: desktopText(
        "继续在上次关闭的项目中检索，或者切换到另一个工作台？",
        "Search in the last project, or switch to another workbench?",
      ),
      detail: workbenchRoot || "",
      buttons: desktopText(
        ["打开检索", "选择其他工作台", "取消"],
        ["Open search", "Choose another workbench", "Cancel"],
      ),
      defaultId: 0,
      cancelId: 2,
    };
    const answer = mainWindow
      ? await dialog.showMessageBox(mainWindow, options)
      : await dialog.showMessageBox(options);
    if (answer.response === 0) mainWindow?.webContents.send("menu", "search.open");
    if (answer.response === 1) await chooseAnotherWorkbench();
  });

  app.whenReady().then(async () => {
    if (!(await ensureWorkbenchSelected())) {
      if (!workbenchRoot) app.quit();
      return;
    }
    buildMenu();
    createWindow();
    const result = await startBackend();
    if (result.error) {
      // The window is already up, so the renderer can show this in context
      // rather than the user getting only a modal with no app behind it.
      mainWindow?.webContents.send("backend:failed", { message: result.error });
      dialog.showErrorBox("PaperCreator: backend did not start", result.error);
    } else {
      mainWindow?.webContents.send("backend:ready", { origin: BACKEND_ORIGIN });
    }
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
  app.on("before-quit", (event) => {
    if (allowQuitAfterBackendExit) return;
    if (pendingAppQuit) {
      // Destroying the last window emits another quit request.  Keep Electron
      // alive until the already-running backend shutdown/checkpoint completes.
      event.preventDefault();
      return;
    }
    event.preventDefault();
    pendingAppQuit = (async () => {
      const prepared = await prepareRendererForQuit();
      if (!prepared.ok) {
        recordBackendLine(
          `quit cancelled: ${prepared.error || "renderer drafts were not saved"}`,
          "stderr",
        );
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.show();
          mainWindow.focus();
          dialog.showErrorBox(
            desktopText("无法安全退出 PaperCreator", "PaperCreator could not quit safely"),
            desktopText(
              `仍有手稿修改未保存。应用会保持打开，请检查错误后重试。\n\n${prepared.error}`,
              `Some manuscript changes were not saved. The app will stay open; resolve the error and try again.\n\n${prepared.error}`,
            ),
          );
        }
        pendingAppQuit = null;
        return;
      }

      const backendShutdown = stopBackendAndWait();
      // Uvicorn cannot finish graceful shutdown while Renderer SSE/polling
      // sockets remain open. Drafts are durable now, so close the UI and wait.
      if (mainWindow && !mainWindow.isDestroyed()) {
        destroyingRendererForQuit = true;
        mainWindow.destroy();
        mainWindow = null;
      }
      await backendShutdown;
      allowQuitAfterBackendExit = true;
      app.quit();
    })().catch((error) => {
      pendingAppQuit = null;
      destroyingRendererForQuit = false;
      recordBackendLine(`quit coordination failed: ${error.message}`, "stderr");
    });
  });
  // A renderer crash must not leave uvicorn holding the port.
  process.on("exit", stopBackend);
  process.on("SIGINT", () => {
    void stopBackendAndWait().finally(() => process.exit(0));
  });
}
