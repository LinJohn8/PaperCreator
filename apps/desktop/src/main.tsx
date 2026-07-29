/** Renderer entry point. */

import React from "react";
import ReactDOM from "react-dom/client";

import { App } from "./App";
import { setBaseUrl } from "./api/client";
import "./styles/theme.css";
import "./styles/app.css";

declare global {
  interface Window {
    papercreator?: {
      isDesktop: boolean;
      appInfo(): Promise<{
        version: string;
        isDev: boolean;
        platform: string;
        backendOrigin: string;
        backendRunning: boolean;
        workbench: string;
        managedDirectory: string;
        backendExecutable: string;
        bundledBackendExists: boolean;
      }>;
      workbench: {
        info(): Promise<{ path: string; managedDirectory: string }>;
        choose(): Promise<{ changed: boolean }>;
      };
      backend: {
        log(): Promise<{ ts: number; stream: string; text: string }[]>;
        restart(): Promise<Record<string, unknown>>;
        onLog(handler: (line: { stream: string; text: string }) => void): () => void;
        onReady(handler: (payload: { origin: string }) => void): () => void;
        onFailed(handler: (payload: { message: string }) => void): () => void;
        onExit(handler: (payload: { code: number | null }) => void): () => void;
      };
      dialog: {
        openDirectory(options?: { title?: string; defaultPath?: string }): Promise<string | null>;
        openFile(filters?: { name: string; extensions: string[] }[]): Promise<string | null>;
        saveJson(options: {
          suggestedName: string;
          data: unknown;
        }): Promise<{ canceled: boolean; path?: string; bytes?: number }>;
        saveAssistantArchive(options: {
          suggestedName: string;
          data: unknown;
          compressed?: boolean;
        }): Promise<{
          canceled: boolean;
          path?: string;
          bytes?: number;
          uncompressedBytes?: number;
          compressed?: boolean;
        }>;
        openAssistantArchive(): Promise<{
          canceled: boolean;
          path?: string;
          data?: unknown;
          bytes?: number;
          uncompressedBytes?: number;
          compressed?: boolean;
        }>;
      };
      shell: {
        showItem(target: string): Promise<void>;
        openPath(target: string): Promise<string>;
        openExternal(url: string): Promise<void>;
        openLogs(): Promise<string>;
      };
      menu: {
        popup(
          id: "File" | "Edit" | "View" | "Help",
          position: { x: number; y: number },
        ): Promise<{ opened: boolean; menu?: string }>;
      };
      lifecycle: {
        onPrepareQuit(
          handler: () => Promise<{ ok: boolean; error?: string }>,
        ): () => void;
      };
      onMenu(handler: (command: string) => void): () => void;
    };
  }
}

async function bootstrap(): Promise<void> {
  // In Electron the page is served over file://, where a relative /api URL has
  // no host. The main process reports the real origin.
  const bridge = window.papercreator;
  if (bridge) {
    try {
      const info = await bridge.appInfo();
      if (info.backendOrigin) setBaseUrl(info.backendOrigin);
    } catch {
      /* fall back to the client's own default */
    }
  }

  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

void bootstrap();
