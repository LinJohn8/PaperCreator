const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

// Some Windows developer hosts export ELECTRON_RUN_AS_NODE globally. Launch a
// clean Electron child explicitly so asset generation is reproducible there.
if (!process.versions.electron) {
  const electronExecutable = require("electron");
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  const parentRuntimeRoot = fs.mkdtempSync(path.join(os.tmpdir(), "papercreator-brand-render-"));
  env.PC_BRAND_RUNTIME_ROOT = parentRuntimeRoot;
  try {
    const result = spawnSync(electronExecutable, [__filename], { env, stdio: "inherit" });
    process.exitCode = result.status ?? 1;
  } finally {
    // The parent only retries after Chromium has fully exited, so locked cache
    // handles cannot leave a permanent build residue behind.
    fs.rmSync(parentRuntimeRoot, { recursive: true, force: true });
  }
  return;
}

const { app, BrowserWindow } = require("electron");

const desktopRoot = path.resolve(__dirname, "..");
const source = path.join(desktopRoot, "assets", "brand", "icon.svg");
const destination = path.join(desktopRoot, "assets", "brand", "icon.png");
const runtimeRoot =
  process.env.PC_BRAND_RUNTIME_ROOT ??
  fs.mkdtempSync(path.join(os.tmpdir(), "papercreator-brand-render-"));
delete process.env.PC_BRAND_RUNTIME_ROOT;
const userData = path.join(runtimeRoot, "user-data");
const sessionData = path.join(runtimeRoot, "session-data");
fs.mkdirSync(userData, { recursive: true });
fs.mkdirSync(sessionData, { recursive: true });
app.setPath("userData", userData);
app.setPath("sessionData", sessionData);

app.commandLine.appendSwitch("force-device-scale-factor", "1");
app.disableHardwareAcceleration();
app.commandLine.appendSwitch("disable-gpu");
app.commandLine.appendSwitch("disable-gpu-compositing");
app.commandLine.appendSwitch("disable-gpu-sandbox");
app.commandLine.appendSwitch("in-process-gpu");
app.commandLine.appendSwitch("disk-cache-dir", path.join(runtimeRoot, "cache"));

async function render() {
  const window = new BrowserWindow({
    width: 1024,
    height: 1024,
    useContentSize: true,
    frame: false,
    show: false,
    transparent: true,
    backgroundColor: "#00000000",
    webPreferences: {
      offscreen: true,
      sandbox: false,
    },
  });

  try {
    await window.loadFile(source);
    await window.webContents.executeJavaScript(
      "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
    );
    const image = await window.webContents.capturePage({ x: 0, y: 0, width: 1024, height: 1024 });
    const size = image.getSize();
    if (size.width !== 1024 || size.height !== 1024) {
      throw new Error(`Unexpected brand image size: ${size.width}x${size.height}`);
    }
    const bitmap = image.toBitmap();
    const alphaAt = (x, y) => bitmap[(y * size.width + x) * 4 + 3];
    if (alphaAt(0, 0) !== 0 || alphaAt(512, 512) !== 255) {
      throw new Error("Brand image alpha contract failed (transparent corner / opaque centre)");
    }
    const png = image.toPNG();
    fs.writeFileSync(destination, png);
    process.stdout.write(`Rendered ${path.relative(desktopRoot, destination)} (${size.width}x${size.height}, ${png.length} bytes)\n`);
  } finally {
    window.destroy();
  }
}

app.whenReady()
  .then(render)
  .then(() => app.quit())
  .catch((error) => {
    process.stderr.write(`${error.stack || error.message}\n`);
    app.exit(1);
  });

app.on("will-quit", () => {
  try {
    fs.rmSync(runtimeRoot, { recursive: true, force: true });
  } catch {
    // The OS temp directory can safely reclaim a locked Chromium cache later.
  }
});
