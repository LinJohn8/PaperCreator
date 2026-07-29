/**
 * Preload bridge.
 *
 * The renderer runs with `contextIsolation: true` and no Node access, so this is
 * the only surface it has into the main process. Everything exposed here is an
 * explicit, narrow capability - there is no generic "invoke any channel" escape
 * hatch, because the renderer displays remote content (paper abstracts, LLM
 * output) and must not be able to reach the filesystem directly.
 */

const { contextBridge, ipcRenderer } = require("electron");

/** Subscribe to a main-process event; returns an unsubscribe function. */
function listen(channel, handler) {
  const wrapped = (_event, payload) => handler(payload);
  ipcRenderer.on(channel, wrapped);
  return () => ipcRenderer.removeListener(channel, wrapped);
}

contextBridge.exposeInMainWorld("papercreator", {
  /** Marks that the UI is running inside Electron rather than a browser tab. */
  isDesktop: true,

  appInfo: () => ipcRenderer.invoke("app:info"),

  workbench: {
    info: () => ipcRenderer.invoke("workbench:info"),
    choose: () => ipcRenderer.invoke("workbench:choose"),
  },

  backend: {
    log: () => ipcRenderer.invoke("backend:log"),
    restart: () => ipcRenderer.invoke("backend:restart"),
    onLog: (handler) => listen("backend:log", handler),
    onReady: (handler) => listen("backend:ready", handler),
    onFailed: (handler) => listen("backend:failed", handler),
    onExit: (handler) => listen("backend:exit", handler),
  },

  dialog: {
    openDirectory: (options) => ipcRenderer.invoke("dialog:openDirectory", options),
    openFile: (filters) => ipcRenderer.invoke("dialog:openFile", filters),
    saveJson: (options) => ipcRenderer.invoke("dialog:saveJson", options),
    saveAssistantArchive: (options) => ipcRenderer.invoke("dialog:saveAssistantArchive", options),
    openAssistantArchive: () => ipcRenderer.invoke("dialog:openAssistantArchive"),
  },

  shell: {
    /** Reveal a file in Explorer/Finder - the usual "where is my export?" action. */
    showItem: (target) => ipcRenderer.invoke("shell:showItem", target),
    openPath: (target) => ipcRenderer.invoke("shell:openPath", target),
    openExternal: (url) => ipcRenderer.invoke("shell:openExternal", url),
    openLogs: () => ipcRenderer.invoke("app:openLogs"),
  },

  menu: {
    popup: (id, position) => ipcRenderer.invoke("menu:popup", id, position),
  },

  lifecycle: {
    /** Save renderer-owned drafts before main closes the backend and window. */
    onPrepareQuit: (handler) => {
      const wrapped = async (_event, payload) => {
        try {
          const result = await handler();
          ipcRenderer.send("lifecycle:prepared", {
            id: payload?.id,
            ok: result?.ok !== false,
            error: String(result?.error ?? ""),
          });
        } catch (error) {
          ipcRenderer.send("lifecycle:prepared", {
            id: payload?.id,
            ok: false,
            error: error instanceof Error ? error.message : String(error),
          });
        }
      };
      ipcRenderer.on("lifecycle:prepare-quit", wrapped);
      ipcRenderer.send("lifecycle:ready");
      return () => ipcRenderer.removeListener("lifecycle:prepare-quit", wrapped);
    },
  },

  /** Native menu accelerators (Ctrl+S, Ctrl+Shift+P, ...) routed to the UI. */
  onMenu: (handler) => listen("menu", handler),
});
