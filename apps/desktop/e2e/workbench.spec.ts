import { _electron as electron, expect, test } from "@playwright/test";
import type { ElectronApplication, Page } from "@playwright/test";
import { execFile } from "node:child_process";
import { promises as fs } from "node:fs";
import { createServer as createHttpServer } from "node:http";
import { createServer as createNetServer } from "node:net";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { gunzipSync } from "node:zlib";

const desktopRoot = path.resolve(__dirname, "..");
const repositoryRoot = path.resolve(desktopRoot, "..", "..");
const python = path.join(repositoryRoot, ".venv", "Scripts", "python.exe");
const execFileAsync = promisify(execFile);

async function git(cwd: string, args: string[]): Promise<void> {
  await execFileAsync("git", args, { cwd, windowsHide: true });
}

async function filesBelow(root: string): Promise<string[]> {
  const found: string[] = [];
  async function walk(directory: string): Promise<void> {
    const entries = await fs.readdir(directory, { withFileTypes: true }).catch(() => []);
    for (const entry of entries) {
      const candidate = path.join(directory, entry.name);
      if (entry.isDirectory()) await walk(candidate);
      else if (entry.isFile()) found.push(candidate);
    }
  }
  await walk(root);
  return found;
}

async function removeTemporaryWorkbench(candidate: string): Promise<void> {
  const resolved = path.resolve(candidate);
  const temporaryRoot = path.resolve(tmpdir());
  if (
    !resolved.startsWith(temporaryRoot + path.sep) ||
    !path.basename(resolved).startsWith("papercreator-e2e-")
  ) {
    throw new Error(`refusing to remove a path outside the E2E temporary boundary: ${resolved}`);
  }
  // The app now waits for Python exit and a successful WAL checkpoint. Windows
  // Defender/indexing can still hold a newly truncated WAL for several seconds
  // after every application handle is closed. Retry only this narrow,
  // already-validated test directory; every other error remains actionable.
  for (let attempt = 0; attempt < 30; attempt += 1) {
    try {
      await fs.rm(resolved, { recursive: true, force: true });
      return;
    } catch (error) {
      const code = (error as NodeJS.ErrnoException).code;
      if (!["EBUSY", "EPERM", "ENOTEMPTY"].includes(String(code))) throw error;
      if (attempt === 29) {
        // Product lifecycle correctness is asserted separately from its logs
        // and process exit.  A host scanner retaining the final temp file must
        // not turn a passing application workflow into a false negative.
        console.warn(`Windows retained an isolated E2E temp file after bounded cleanup: ${resolved}`);
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, Math.min(1000, 100 + attempt * 75)));
    }
  }
}

/** Close through Electron's real app lifecycle and wait for the owned backend. */
async function closeApplication(application: ElectronApplication): Promise<void> {
  const child = application.process();
  if (child.exitCode !== null || child.signalCode !== null) return;

  const waitForExit = (timeoutMs: number) => new Promise<boolean>((resolve) => {
    if (child.exitCode !== null || child.signalCode !== null) {
      resolve(true);
      return;
    }
    const timer = setTimeout(() => {
      child.off("exit", onExit);
      resolve(false);
    }, timeoutMs);
    const onExit = () => {
      clearTimeout(timer);
      resolve(true);
    };
    child.once("exit", onExit);
  });

  await application.evaluate(({ app }) => app.quit()).catch(() => undefined);
  if (await waitForExit(20_000)) return;

  const pid = child.pid;
  if (!pid) throw new Error("Electron did not exit gracefully and has no owned PID to terminate");

  if (process.platform === "win32") {
    await execFileAsync("taskkill", ["/PID", String(pid), "/T", "/F"], {
      windowsHide: true,
    }).catch((error) => {
      if (child.exitCode === null && child.signalCode === null) throw error;
    });
  } else {
    child.kill("SIGKILL");
  }

  if (!(await waitForExit(10_000))) {
    throw new Error(`Electron process tree ${pid} remained alive after forced termination`);
  }
}

/** Read ZIP central-directory names without adding a test-only dependency. */
function zipEntryNames(archive: Buffer): string[] {
  const names: string[] = [];
  const signature = Buffer.from([0x50, 0x4b, 0x01, 0x02]);
  let offset = 0;
  while ((offset = archive.indexOf(signature, offset)) !== -1) {
    if (offset + 46 > archive.length) break;
    const nameLength = archive.readUInt16LE(offset + 28);
    const extraLength = archive.readUInt16LE(offset + 30);
    const commentLength = archive.readUInt16LE(offset + 32);
    const nameStart = offset + 46;
    const nameEnd = nameStart + nameLength;
    if (nameEnd > archive.length) break;
    names.push(archive.subarray(nameStart, nameEnd).toString("utf8").replace(/\\/g, "/"));
    offset = nameEnd + extraLength + commentLength;
  }
  return names;
}

function bibliographyFixture(): string {
  const entries = [
    ["Graph Neural Networks for Molecular Property Prediction", "Graph message passing learns molecular properties from atoms and chemical bonds.", "molecular graphs, graph neural networks, chemistry"],
    ["Message Passing Networks for Quantum Chemistry", "Message passing predicts quantum energy and molecular interactions.", "molecular graphs, message passing, quantum chemistry"],
    ["Geometric Transformers for Protein Ligand Binding", "Equivariant geometric attention models protein ligand binding structures.", "geometric learning, proteins, molecular binding"],
    ["Self Supervised Molecular Graph Representation Learning", "Self supervised objectives improve molecular graph encoders for drug discovery.", "molecular graphs, self supervised learning, drug discovery"],
    ["Efficient Retrieval Augmented Language Models", "Retrieval augmented generation grounds language models in external documents.", "language models, retrieval augmented generation, grounding"],
    ["Instruction Tuning for Scientific Reasoning", "Instruction tuned language models solve structured scientific reasoning tasks.", "language models, instruction tuning, scientific reasoning"],
    ["Long Context Transformers with Sparse Attention", "Sparse attention extends transformer language models to long research documents.", "language models, sparse attention, long context"],
    ["Multi Agent Language Models for Research Planning", "Multiple language model agents coordinate planning critique and scientific writing.", "language models, multi agent systems, research planning"],
    ["Vision Transformers for Medical Image Segmentation", "Vision transformers segment organs and lesions in medical images.", "computer vision, transformers, medical segmentation"],
    ["Contrastive Learning for Visual Recognition", "Contrastive representation learning improves image recognition with limited labels.", "computer vision, contrastive learning, recognition"],
    ["Diffusion Models for Image Synthesis", "Denoising diffusion models generate high fidelity images from text prompts.", "computer vision, diffusion models, image generation"],
    ["Three Dimensional Scene Reconstruction with Neural Fields", "Neural radiance fields reconstruct three dimensional scenes from images.", "computer vision, neural fields, scene reconstruction"],
  ];
  return entries
    .map(
      ([title, abstract, keywords], index) => `@article{e2e${index + 1},
  title = {${title}},
  author = {Researcher, Alice and Scientist, Bob},
  year = {${2012 + index}},
  journal = {E2E Research Transactions},
  doi = {10.5555/papercreator.e2e.${index + 1}},
  abstract = {${abstract}},
  keywords = {${keywords}}
}`,
    )
    .join("\n\n") + "\n";
}

async function reservePort(): Promise<number> {
  const server = createNetServer();
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new Error("could not reserve a loopback port");
  }
  await new Promise<void>((resolve, reject) =>
    server.close((error) => (error ? reject(error) : resolve())),
  );
  return address.port;
}

type MockLlmRequest = {
  authorization: string;
  messages: Array<{ role: string; content: string }>;
  stream: boolean;
};

async function startMockLlm(): Promise<{
  baseUrl: string;
  requests: MockLlmRequest[];
  failNextStream(): void;
  close(): Promise<void>;
}> {
  const requests: MockLlmRequest[] = [];
  let streamsToInterrupt = 0;
  const server = createHttpServer((request, response) => {
    if (request.method !== "POST" || request.url !== "/v1/chat/completions") {
      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: "unknown E2E mock route" }));
      return;
    }
    const chunks: Buffer[] = [];
    request.on("data", (chunk: Buffer) => chunks.push(chunk));
    request.on("end", () => {
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8")) as {
        messages?: Array<{ role?: string; content?: string }>;
        model?: string;
        stream?: boolean;
      };
      const messages = (body.messages ?? []).map((message) => ({
        role: String(message.role ?? ""),
        content: String(message.content ?? ""),
      }));
      const system = messages.find((message) => message.role === "system")?.content ?? "";
      const stream = body.stream === true;
      requests.push({
        authorization: String(request.headers.authorization ?? ""),
        messages,
        stream,
      });

      let content: string;
      if (system.includes("reading agent")) {
        content = JSON.stringify({
          summary: "A molecular graph study used by the deterministic E2E agent.",
          problem: "Reliable molecular property prediction.",
          method: "Graph message passing over atoms and bonds.",
          findings: "The abstract reports improved predictive representations.",
          limitations: "Only abstract-level evidence is available in this fixture.",
          relevance: "It grounds the paper's graph-language-model motivation.",
          relevance_score: 0.91,
          use_for_sections: ["introduction"],
        });
      } else if (system.includes("synthesis agent")) {
        content = JSON.stringify({
          themes: [
            {
              name: "Molecular graph representation learning",
              description: "Graph encoders learn chemical structure representations.",
              paper_keys: ["RESEARCHER2012"],
              consensus: "Molecular structure benefits from graph inductive bias.",
              disagreement: "How best to combine graph and language representations.",
              maturity: "active",
            },
          ],
          chronology: "Graph prediction preceded multi-agent scientific workflows.",
          methodological_split: "Message passing versus transformer-style models.",
          evaluation_practice: "Held-out molecular property benchmarks.",
        });
      } else if (system.includes("drafting agent")) {
        content = [
          "Molecular discovery increasingly depends on representations that preserve chemical structure while supporting flexible scientific reasoning [RESEARCHER2012].",
          "The imported literature shows that graph inductive biases provide a concrete foundation for molecular prediction, while language-model agents add planning and synthesis capabilities.",
          "This deterministic E2E agent draft was streamed through the real OpenAI-compatible client and persisted by the section-writing pipeline.",
        ].join(" ");
      } else if (system.includes("translation agent")) {
        content = [
          "分子发现日益依赖既能保留化学结构、又能支持灵活科学推理的表征 [RESEARCHER2012]。",
          "这段确定性的 E2E 智能体译文通过真实的 OpenAI 兼容流式客户端生成，并由双语章节流水线持久化。",
        ].join("");
      } else {
        content = JSON.stringify({ ok: true });
      }

      if (!stream) {
        response.writeHead(200, { "Content-Type": "application/json" });
        response.end(
          JSON.stringify({
            id: `chatcmpl-e2e-${requests.length}`,
            object: "chat.completion",
            model: body.model ?? "gpt-4o-mini",
            choices: [
              { index: 0, message: { role: "assistant", content }, finish_reason: "stop" },
            ],
            usage: { prompt_tokens: 31, completion_tokens: 17, total_tokens: 48 },
          }),
        );
        return;
      }

      response.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      });
      const midpoint = Math.ceil(content.length / 2);
      const deltas = [content.slice(0, midpoint), content.slice(midpoint)];
      for (const delta of deltas) {
        response.write(
          `data: ${JSON.stringify({
            id: `chatcmpl-e2e-${requests.length}`,
            object: "chat.completion.chunk",
            model: body.model ?? "gpt-4o-mini",
            choices: [{ index: 0, delta: { content: delta }, finish_reason: null }],
          })}\n\n`,
        );
        if (streamsToInterrupt > 0) {
          streamsToInterrupt -= 1;
          // A clean EOF without the protocol's [DONE] marker is how proxies and
          // local gateways commonly truncate a stream. The backend must reject
          // it while retaining the first delta in the failed-step audit.
          response.end();
          return;
        }
      }
      response.write(
        `data: ${JSON.stringify({
          id: `chatcmpl-e2e-${requests.length}`,
          object: "chat.completion.chunk",
          model: body.model ?? "gpt-4o-mini",
          choices: [{ index: 0, delta: {}, finish_reason: "stop" }],
          usage: { prompt_tokens: 47, completion_tokens: 53, total_tokens: 100 },
        })}\n\n`,
      );
      response.end("data: [DONE]\n\n");
    });
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new Error("could not start the local E2E LLM service");
  }
  return {
    baseUrl: `http://127.0.0.1:${address.port}/v1`,
    requests,
    failNextStream: () => {
      streamsToInterrupt += 1;
    },
    close: () =>
      new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      ),
  };
}

async function startMockOpenAlex(): Promise<{
  endpoint: string;
  requests: string[];
  close(): Promise<void>;
}> {
  const requests: string[] = [];
  const server = createHttpServer((request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    requests.push(url.toString());
    if (url.pathname !== "/works") {
      response.writeHead(404, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ error: "not found" }));
      return;
    }

    // HttpClient retries a 429 three times. Keep all four transport attempts
    // rate-limited so the first user search reaches the diagnostic UI; the next
    // request is the explicit recovery retry and succeeds deterministically.
    if (requests.length <= 4) {
      response.writeHead(429, {
        "Content-Type": "application/json",
        "Retry-After": "0.01",
      });
      response.end(JSON.stringify({ error: "deterministic E2E quota" }));
      return;
    }

    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify({
      meta: { count: 1, next_cursor: null },
      results: [{
        id: "https://openalex.org/W5555000001",
        doi: "https://doi.org/10.5555/papercreator.e2e.1",
        title: "Graph Neural Networks for Molecular Property Prediction",
        publication_year: 2012,
        publication_date: "2012-01-01",
        type: "article",
        cited_by_count: 37,
        referenced_works_count: 0,
        referenced_works: [],
        language: "en",
        abstract_inverted_index: {
          Graph: [0], message: [1], passing: [2], learns: [3], molecular: [4],
          properties: [5], from: [6], atoms: [7], and: [8], bonds: [9],
        },
        authorships: [{
          author: { display_name: "Alice Researcher" },
          institutions: [{ display_name: "E2E University" }],
        }],
        primary_location: {
          landing_page_url: "https://example.test/e2e-paper",
          pdf_url: null,
          source: { display_name: "E2E Research Transactions" },
        },
        open_access: { is_oa: true, oa_url: "https://example.test/e2e-paper.pdf" },
        topics: [{ display_name: "Graph Neural Networks" }],
        keywords: [{ display_name: "molecular graphs" }],
        ids: {},
        is_retracted: false,
      }],
    }));
  });
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    server.close();
    throw new Error("could not start the local E2E OpenAlex service");
  }
  return {
    endpoint: `http://127.0.0.1:${address.port}/works`,
    requests,
    close: () =>
      new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      ),
  };
}

async function launch(
  workbench: string,
  llmBaseUrl: string,
  openAlexEndpoint: string,
  configureLlm = true,
): Promise<{
  application: ElectronApplication;
  page: Page;
}> {
  const port = await reservePort();
  const environment: Record<string, string> = {};
  for (const [key, value] of Object.entries(process.env)) {
    if (value !== undefined) environment[key] = value;
  }
  Object.assign(environment, {
    PAPERCREATOR_WORKBENCH: workbench,
    PC_PORT: String(port),
    PC_PYTHON: python,
    PC_OFFLINE_MODELS: "1",
    PC_E2E: "1",
    PC_E2E_OPEN_FILE: path.join(workbench, "e2e-reference-papers.bib"),
    PC_E2E_OPEN_DIRECTORY: path.join(workbench, "e2e-code-source"),
    PC_E2E_SAVE_JSON: path.join(workbench, "e2e-assistant-conversations.json"),
    PC_E2E_SAVE_ASSISTANT_ARCHIVE: path.join(workbench, "e2e-assistant-conversations.json.gz"),
    PC_E2E_OPEN_ASSISTANT_ARCHIVE: path.join(workbench, "e2e-assistant-conversations.json.gz"),
    PC_OPENALEX_ENDPOINT: openAlexEndpoint,
    ELECTRON_DISABLE_SECURITY_WARNINGS: "true",
    ELECTRON_ENABLE_LOGGING: "true",
  });
  if (configureLlm) {
    environment.PC_OPENAI_API_KEY = "papercreator-e2e-local-only";
    environment.PC_OPENAI_BASE_URL = llmBaseUrl;
  } else {
    // Empty values intentionally stay in the child environment.  Deleting the
    // variables would let the backend's non-clobbering .env loader repopulate
    // credentials from a developer checkout and make this "no model" restart
    // depend on the host machine.  Cover every provider recognised by
    // _llm_providers_from_env(), including keyless Ollama.
    for (const key of [
      "PC_OPENAI_API_KEY",
      "PC_ANTHROPIC_API_KEY",
      "PC_GEMINI_API_KEY",
      "PC_DEEPSEEK_API_KEY",
      "PC_OPENROUTER_API_KEY",
      "PC_OLLAMA_BASE_URL",
    ]) {
      environment[key] = "";
    }
    environment.PC_OPENAI_BASE_URL = "";
  }
  // Some IDE/agent hosts set this to make electron.exe behave as Node. It must
  // never leak into an actual Electron launch or Chromium flags are rejected.
  delete environment.ELECTRON_RUN_AS_NODE;
  const application = await electron.launch({
    args: [desktopRoot],
    cwd: desktopRoot,
    env: environment,
    timeout: 60_000,
  });
  const launchDiagnostics: string[] = [];
  const child = application.process();
  child.stdout?.on("data", (chunk) => launchDiagnostics.push(`stdout: ${String(chunk)}`));
  child.stderr?.on("data", (chunk) => launchDiagnostics.push(`stderr: ${String(chunk)}`));
  let page: Page;
  try {
    page = await application.firstWindow({ timeout: 60_000 });
  } catch (error) {
    await new Promise((resolve) => setTimeout(resolve, 150));
    throw new Error(
      `Electron exited before creating its first window: ${String(error)}\n${launchDiagnostics.join("\n")}`,
    );
  }
  await expect
    .poll(async () => {
      const info = await page.evaluate(() =>
        (window as unknown as {
          papercreator: { appInfo(): Promise<{ backendRunning: boolean }> };
        }).papercreator.appInfo(),
      );
      return info.backendRunning;
    }, { timeout: 90_000, message: "the real backend should become ready" })
    .toBe(true);
  // The first launch opens the workbench home, while later launches may restore
  // the last paper quickly enough that the home heading never paints.  The
  // callers assert their expected view; this helper only waits for boot to leave
  // the transient backend-loading shell.
  await expect(page.locator(".shell:not(.transient-shell)")).toBeVisible();
  return { application, page };
}

test("core workbench, version and export workflow survives real restarts", async ({}, testInfo) => {
  const workbench = await fs.mkdtemp(path.join(tmpdir(), "papercreator-e2e-"));
  const managed = path.join(workbench, ".papercreator");
  const bibliographyPath = path.join(workbench, "e2e-reference-papers.bib");
  const codeSource = path.join(workbench, "e2e-code-source");
  const codeImportTitle = `E2E managed code ${Date.now()}`;
  const ideaTitle = `E2E idea ${Date.now()}`;
  const projectTitle = `E2E paper ${Date.now()}`;
  const manuscriptText = [
    "# Reproducible E2E manuscript",
    "",
    "This paragraph was written through the real CodeMirror editor.",
    "It must survive backend and Electron restarts. [RESEARCHER2012]",
  ].join("\n");
  const revisedText = [
    "# Temporary revision",
    "",
    "This text must disappear when the saved snapshot is restored.",
  ].join("\n");
  const branchText = [
    "# Branch-only rewrite",
    "",
    "This manuscript belongs only to the E2E exploration branch.",
  ].join("\n");
  const externalDiskMarker =
    "External manuscript edit that PaperCreator must preserve before overwriting.";
  const databasePreferredMarker =
    "Database version accepted through the explicit conflict resolver.";
  const filePreferredMarker =
    "External file version accepted through the explicit conflict resolver.";
  const disjointDiskMarker = "DISJOINT-DISK-SECTION-EDIT";
  const disjointDatabaseMarker = "DISJOINT-DATABASE-SECTION-EDIT";
  const agentDraftMarker =
    "This deterministic E2E agent draft was streamed through the real OpenAI-compatible client";
  const agentTranslationMarker = "这段确定性的 E2E 智能体译文";
  const versionLabel = `E2E baseline ${Date.now()}`;
  const branchName = `e2e-rewrite-${Date.now()}`;
  const branchVersionLabel = `E2E branch rewrite ${Date.now()}`;
  const postRemoteVersionLabel = `E2E post-remote baseline ${Date.now()}`;
  const divergentVersionLabel = `E2E local divergent commit ${Date.now()}`;
  const remoteManuscriptMarker =
    "Remote collaborator text delivered through the safe fast-forward Git workflow.";
  const consoleErrors: string[] = [];
  let expectedRemoteConflictConsole = false;
  const mockLlm = await startMockLlm();
  const mockOpenAlex = await startMockOpenAlex();
  let running: ElectronApplication | null = null;

  try {
    await fs.writeFile(bibliographyPath, bibliographyFixture(), "utf8");
    await fs.mkdir(path.join(codeSource, "src", "parts"), { recursive: true });
    await fs.mkdir(path.join(codeSource, "node_modules", "ignored"), { recursive: true });
    await Promise.all(
      Array.from({ length: 64 }, (_, index) =>
        fs.writeFile(
          path.join(codeSource, "src", "parts", `module-${index}.py`),
          `# deterministic managed import fixture ${index}\nVALUE = ${index}\n`,
          "utf8",
        ),
      ),
    );
    await fs.writeFile(path.join(codeSource, ".env"), "SECRET=must-not-copy\n", "utf8");
    await fs.writeFile(path.join(codeSource, ".env.example"), "SECRET=\n", "utf8");
    await fs.writeFile(
      path.join(codeSource, "node_modules", "ignored", "index.js"),
      "throw new Error('must not copy');\n",
      "utf8",
    );
    const first = await launch(workbench, mockLlm.baseUrl, mockOpenAlex.endpoint);
    running = first.application;
    const onPageError = (error: Error) => consoleErrors.push(error.message);
    const onConsole = (message: { type(): string; text(): string }) => {
      if (message.type() !== "error") return;
      const text = message.text();
      if (expectedRemoteConflictConsole && text.includes("status of 409")) {
        expectedRemoteConflictConsole = false;
        return;
      }
      consoleErrors.push(text);
    };
    first.page.on("pageerror", onPageError);
    first.page.on("console", onConsole);

    const info = await first.page.evaluate(() =>
      (window as unknown as {
        papercreator: {
          appInfo(): Promise<{
            workbench: string;
            managedDirectory: string;
            backendOrigin: string;
          }>;
        };
      }).papercreator.appInfo(),
    );
    expect(path.resolve(info.workbench)).toBe(path.resolve(workbench));
    expect(path.resolve(info.managedDirectory)).toBe(path.resolve(managed));
    expect(info.backendOrigin).toMatch(/^http:\/\/127\.0\.0\.1:\d+$/);

    // Windows uses one VS Code-style title row: product mark before File,
    // project identity in the centre, app actions before native window controls.
    const titlebar = first.page.locator(".titlebar");
    const logo = titlebar.locator(".titlebar-logo");
    const fileMenu = titlebar.getByRole("button", { name: /^(文件|File)$/ });
    const commandButton = titlebar.getByRole("button", { name: /^(命令面板|Command Palette)$/ });
    await expect(titlebar).toBeVisible();
    await expect(logo).toBeVisible();
    await expect(fileMenu).toBeVisible();
    await expect(commandButton).toBeVisible();
    const titlebarGeometry = await Promise.all([
      logo.boundingBox(),
      fileMenu.boundingBox(),
      commandButton.boundingBox(),
    ]);
    expect(titlebarGeometry.every(Boolean)).toBe(true);
    expect(titlebarGeometry[0]!.x).toBeLessThan(titlebarGeometry[1]!.x);
    expect(titlebarGeometry[1]!.x).toBeLessThan(titlebarGeometry[2]!.x);
    expect(
      await first.page.evaluate(() =>
        typeof (window as unknown as {
          papercreator?: { menu: { popup: unknown } };
        }).papercreator?.menu.popup,
      ),
    ).toBe("function");
    expect(
      await first.application.evaluate(({ BrowserWindow }) =>
        BrowserWindow.getAllWindows()[0]?.isMenuBarVisible(),
      ),
    ).toBe(false);
    const helpLabels = await first.application.evaluate(({ Menu }) =>
      Menu.getApplicationMenu()?.items
        .find((item) => item.id === "Help")
        ?.submenu?.items.map((item) => item.label) ?? [],
    );
    expect(helpLabels).toContain("快速开始");
    expect(helpLabels).toContain("打开日志目录");
    await expect(first.page.evaluate(() =>
      (window as unknown as {
        papercreator: { shell: { openLogs(): Promise<string> } };
      }).papercreator.shell.openLogs(),
    )).resolves.toBe("");

    // A genuinely empty workbench gets a task-based guide whose progress comes
    // from product state. Verify it remains usable at the desktop minimum size
    // as well as the normal first-run window size.
    const quickStart = first.page.getByRole("dialog", { name: /^(快速开始|Quick start)$/ });
    await expect(quickStart).toBeVisible();
    await expect(quickStart.locator(".quick-start-steps > li")).toHaveCount(5);
    await expect(quickStart).toContainText(/已完成 1 \/ 5|1 of 5 complete/);
    for (const [width, height] of [[1365, 900], [1100, 700]] as const) {
      await first.application.evaluate(({ BrowserWindow }, size) => {
        BrowserWindow.getAllWindows()[0]?.setSize(size.width, size.height);
      }, { width, height });
      await expect(quickStart).toBeInViewport();
      await expect(quickStart.getByRole("button", { name: /^(稍后再说|Later)$/ })).toBeVisible();
      const imagePath = testInfo.outputPath(`quick-start-${width}x${height}.png`);
      await first.page.screenshot({ path: imagePath, fullPage: true });
      await testInfo.attach(`quick-start-${width}x${height}`, {
        path: imagePath,
        contentType: "image/png",
      });
    }
    await first.application.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0]?.setSize(1680, 1000),
    );
    await quickStart.getByRole("button", { name: /^(稍后再说|Later)$/ }).click();
    await expect(quickStart).toBeHidden();

    await commandButton.click();
    const palette = first.page.getByRole("dialog", { name: /^(命令面板|Command palette)$/ });
    await palette.locator("input").fill("快速开始");
    await palette.getByText("打开快速开始", { exact: true }).click();
    await expect(quickStart).toBeVisible();
    await quickStart.getByRole("button", { name: /^(稍后再说|Later)$/ }).click();

    // Native File > New Project and the page button share the same actionable
    // creator rather than merely navigating to the workbench.
    await first.application.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0]?.webContents.send("menu", "project.new"),
    );
    let modal = first.page.getByRole("dialog", { name: /^(新建论文项目|New paper project)$/ });
    await expect(modal).toBeVisible();
    await modal.getByRole("button", { name: /^(关闭新建项目|Close new project)$/ }).click();
    await expect(modal).toBeHidden();

    await first.page.getByRole("button", { name: /^(记录 Idea|Add idea)$/ }).click();
    modal = first.page.locator(".modal");
    await modal.locator("input").first().fill(ideaTitle);
    await modal.locator("textarea").fill(
      "A deterministic Electron test idea stored only in the temporary workbench.",
    );
    await modal.getByRole("button", { name: /^(保存 Idea|Save idea)$/ }).click();
    await expect(modal).toBeHidden();
    await expect(first.page.getByText(ideaTitle, { exact: true })).toBeVisible();

    const codeCard = first.page.locator(".card").filter({
      has: first.page.getByRole("heading", { name: /^(项目代码|Code projects)/ }),
    });
    await codeCard.getByRole("button", { name: /^(导入|Import)$/ }).click();
    modal = first.page.locator(".modal");
    await modal.getByRole("button", { name: /^(选择目录|Choose folder)$/ }).click();
    await expect(modal.locator('input[readonly]')).toHaveValue(path.resolve(codeSource));
    await modal.locator('input:not([readonly])').fill(codeImportTitle);
    await modal.getByRole("button", { name: /^(导入托管副本|Import managed copy)$/ }).click();
    await expect(modal).toBeHidden({ timeout: 30_000 });
    await expect(first.page.getByText(codeImportTitle, { exact: true })).toBeVisible();

    const codeImport = await first.page.evaluate(async (origin) => {
      const [resourcesResponse, jobsResponse] = await Promise.all([
        fetch(`${origin}/api/workbench/resources?kind=code_project`),
        fetch(`${origin}/api/system/jobs?limit=30`),
      ]);
      const resources = (await resourcesResponse.json()) as {
        items: Array<{
          title: string;
          path: string;
          metadata: {
            import?: {
              strategy?: string;
              source_files?: number;
              copied_files?: number;
              link_policy?: string;
            };
            excluded_from_copy?: string[];
          };
        }>;
      };
      const jobs = (await jobsResponse.json()) as {
        items: Array<{ kind: string; status: string; result: Record<string, unknown> }>;
      };
      return {
        resource: resources.items.find((item) => item.title.includes("E2E managed code")),
        job: jobs.items.find((item) => item.kind === "resource_import"),
      };
    }, info.backendOrigin);
    expect(codeImport.resource?.metadata.import).toMatchObject({
      strategy: "atomic_managed_copy",
      source_files: 65,
      copied_files: 65,
      link_policy: "never_follow",
    });
    expect(codeImport.resource?.metadata.excluded_from_copy).toEqual(
      expect.arrayContaining([".env", "node_modules"]),
    );
    expect(codeImport.job?.status).toBe("done");
    const managedCode = String(codeImport.resource?.path ?? "");
    expect((await fs.stat(path.join(managedCode, "src", "parts", "module-63.py"))).isFile()).toBe(true);
    expect((await fs.stat(path.join(managedCode, ".env.example"))).isFile()).toBe(true);
    await expect(fs.stat(path.join(managedCode, ".env"))).rejects.toThrow();
    await expect(fs.stat(path.join(managedCode, "node_modules"))).rejects.toThrow();
    const codeCategoryNames = await fs.readdir(path.dirname(managedCode));
    expect(codeCategoryNames.some((name) => name.startsWith(".partial-res_"))).toBe(false);

    await first.page.getByRole("button", { name: /^(新建项目|New project)$/ }).click();
    modal = first.page.locator(".modal");
    await modal.locator("input").first().fill(projectTitle);
    await modal.locator("textarea").first().fill(
      "Verify that project state and generated sections survive an Electron restart.",
    );
    await modal.locator('input[type="checkbox"]').nth(1).uncheck();
    await modal.getByRole("button", { name: /^(创建|Create)$/ }).click();
    await expect(modal).toBeHidden({ timeout: 30_000 });
    await expect(first.page.locator(".titlebar .project-pill .name")).toHaveText(projectTitle);
    await expect(first.page.locator(".editor-pane .cm-content").first()).toBeVisible();

    // Help can reopen the guide after first use. Imported material and the new
    // project are reflected automatically, and the explicit preference is
    // persisted by the backend rather than local browser storage.
    await first.application.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0]?.webContents.send("menu", "help.quickStart"),
    );
    await expect(quickStart).toBeVisible();
    await expect(quickStart.locator(".quick-start-steps > li.complete")).toHaveCount(3);
    await quickStart.getByRole("button", {
      name: /^(不再自动显示|Don't show automatically)$/,
    }).click();
    await expect(quickStart).toBeHidden();
    await expect.poll(async () => {
      const response = await fetch(`${info.backendOrigin}/api/settings`);
      return (await response.json()).ui.quick_start_version;
    }).toBe(1);

    await expect.poll(async () => {
      const ideas = await fs.readdir(path.join(managed, "library", "ideas"));
      const projects = await fs.readdir(path.join(managed, "projects"));
      return {
        database: (await fs.stat(path.join(managed, "papercreator.db"))).isFile(),
        ideas: ideas.filter((name) => name.endsWith(".md")).length,
        projects: projects.length,
      };
    }).toEqual({ database: true, ideas: 1, projects: 1 });

    const [projectDirectory] = await fs.readdir(path.join(managed, "projects"));
    const projectRoot = path.join(managed, "projects", projectDirectory);
    const manuscriptDirectory = path.join(
      projectRoot,
      "manuscript",
    );
    const projectId = await first.page.evaluate(async (origin) => {
      const response = await fetch(`${origin}/api/projects`);
      const payload = (await response.json()) as { items: { id: string }[] };
      return payload.items[0]?.id ?? "";
    }, info.backendOrigin);
    expect(projectId).not.toBe("");

    // Chapter CRUD must preserve independent primary/paired targets and leave
    // the original template intact after the temporary section is removed.
    await first.page.getByRole("button", { name: /^＋ (章节|section)$/ }).click();
    modal = first.page.locator(".modal");
    await modal.getByLabel(/^(稳定键（可留空自动生成）|Stable key \(optional\))$/).fill("e2e-notes");
    await modal.getByLabel(/^(英文名称|English title)$/).fill("E2E Research Notes");
    await modal.getByLabel(/^(中文名称|Chinese title)$/).fill("E2E 附录");
    await modal.getByLabel(/^(主语言目标字数|Primary target)$/).fill("321");
    await modal.getByLabel(/^(对照语言目标字数|Paired target)$/).fill("210");
    await modal.getByLabel(/^(写作要求|Writing brief)$/).fill("A temporary E2E section.");
    await modal.getByRole("button", { name: /^(保存|Save)$/ }).click();
    await expect(modal).toBeHidden();
    await expect(first.page.locator(".editor-tab", { hasText: "E2E 附录" })).toBeVisible();
    await expect(first.page.locator(".sidebar-body")).toContainText("0/321");
    await expect(first.page.locator(".sidebar-body")).toContainText("0/210");
    await first.page.locator(".editor-tab", { hasText: "E2E 附录" }).click();
    await first.page.getByRole("button", { name: /^(章节设置|Section settings)$/ }).click();
    modal = first.page.locator(".modal");
    await modal.getByLabel(/^(中文名称|Chinese title)$/).fill("E2E 研究笔记");
    await modal.getByLabel(/^(主语言目标字数|Primary target)$/).fill("333");
    await modal.getByLabel(/^(对照语言目标字数|Paired target)$/).fill("222");
    await modal.getByRole("button", { name: /^(保存|Save)$/ }).click();
    await expect(modal).toBeHidden();
    await expect(first.page.locator(".editor-tab", { hasText: "E2E 研究笔记" })).toBeVisible();
    await expect(first.page.locator(".sidebar-body")).toContainText("0/333");
    await expect(first.page.locator(".sidebar-body")).toContainText("0/222");
    await first.page.getByRole("button", { name: /^(章节设置|Section settings)$/ }).click();
    modal = first.page.locator(".modal");
    first.page.once("dialog", (dialog) => dialog.accept());
    await modal.getByRole("button", { name: /^(删除章节|Delete section)$/ }).click();
    await expect(modal).toBeHidden();
    await expect(first.page.locator(".editor-tab", { hasText: "E2E 研究笔记" })).toHaveCount(0);

    const assistantPanel = first.page.locator(".assistant-panel");
    if (!(await assistantPanel.isVisible())) {
      await first.page.getByRole("button", { name: /^(AI 助手|AI assistant)$/ }).click();
    }
    const promptTitle = `E2E prompt ${Date.now()}`;
    await assistantPanel.getByRole("button", { name: /^(提示词模板|Prompt templates)$/ }).click();
    modal = first.page.locator(".modal");
    await modal.getByLabel(/^(名称|Name)$/).fill(promptTitle);
    await modal.getByLabel(/^(说明|Description)$/).fill("A project-scoped E2E prompt.");
    await modal.getByLabel(/^(模板内容（用 \{\{variable\}\} 声明变量）|Template content \(variables use \{\{variable\}\}\))$/)
      .fill("Search literature about {{topic}}, explain the evidence gap, and prepare a local Git commit.");
    await modal.getByRole("button", { name: /^(保存|Save)$/ }).click();
    const promptItem = modal.locator(".prompt-template-item").filter({ hasText: promptTitle });
    await expect(promptItem).toBeVisible();
    const storedPrompt = await first.page.evaluate(async ({ origin, title, project }) => {
      const response = await fetch(`${origin}/api/prompts?project_id=${project}`);
      const payload = (await response.json()) as { items: Array<{ name: string; variables: string[] }> };
      return payload.items.find((item) => item.name === title) ?? null;
    }, { origin: info.backendOrigin, title: promptTitle, project: projectId });
    expect(storedPrompt?.variables).toEqual(["topic"]);
    await promptItem.click();
    await modal.getByRole("button", { name: /^(用于对话|Use in chat)$/ }).click();
    modal = first.page.locator(".modal");
    await modal.getByLabel("topic", { exact: true }).fill("molecular graphs");
    await modal.getByRole("button", { name: /^(插入对话|Insert into chat)$/ }).click();
    await expect(modal).toBeHidden();
    await expect(assistantPanel.locator("textarea")).toHaveValue(
      "Search literature about molecular graphs, explain the evidence gap, and prepare a local Git commit.",
    );
    await assistantPanel.getByRole("button", { name: /^(提示词模板|Prompt templates)$/ }).click();
    modal = first.page.locator(".modal");
    await modal.locator(".prompt-template-item").filter({ hasText: promptTitle }).click();
    const deletePromptDialog = first.page.waitForEvent("dialog").then((dialog) => dialog.accept());
    await modal.getByRole("button", { name: /^(删除|Delete)$/ }).click();
    await deletePromptDialog;
    await expect(modal.locator(".prompt-template-item").filter({ hasText: promptTitle })).toHaveCount(0);
    await modal.getByRole("button", { name: /^(关闭提示词模板|Close prompt templates)$/ }).click();

    await first.page.getByRole("button", { name: /^(文献库|Library)$/ }).click();
    await expect(first.page.getByRole("heading", { name: /^(文献库|Paper library)$/ })).toBeVisible();
    await first.page
      .getByRole("button", { name: /^(导入 .bib\/.ris\/.csv|Import .bib\/.ris\/.csv)$/ })
      .click();
    await expect(first.page.getByText(/^(已导入 12 条记录|Imported 12 records)$/)).toBeVisible();
    await expect(first.page.getByText(/12 显示\s*\/\s*12 匹配/)).toBeVisible();
    await expect(
      first.page.getByText("Graph Neural Networks for Molecular Property Prediction", {
        exact: true,
      }),
    ).toBeVisible();
    await expect.poll(async () => {
      const referenceDirectory = path.join(managed, "library", "reference-papers");
      const managedFiles = (await fs.readdir(referenceDirectory)).filter((name) =>
        name.endsWith("-e2e-reference-papers.bib"),
      );
      const managedCopy = managedFiles[0]
        ? path.join(referenceDirectory, managedFiles[0])
        : "";
      return {
        sourceExists: (await fs.stat(bibliographyPath)).isFile(),
        managedCopies: managedFiles.length,
        identical:
          Boolean(managedCopy) &&
          (await fs.readFile(managedCopy, "utf8")) === bibliographyFixture(),
      };
    }).toEqual({ sourceExists: true, managedCopies: 1, identical: true });

    // Exercise both user-facing seed modes through the real background-search
    // job. The provider catalogue must refresh after import so Local files can
    // become available without restarting the app.
    await first.page.getByRole("button", { name: /^(检索|Search)$/ }).click();
    await expect(
      first.page.getByRole("heading", { name: /^(文献检索|Literature search)$/ }),
    ).toBeVisible();
    const localProvider = first.page.getByLabel("search-provider-local");
    await expect(localProvider).toBeVisible();
    const localProviderCheckbox = localProvider.locator('input[type="checkbox"]');
    await expect(localProviderCheckbox).toBeEnabled();
    const providerCheckboxes = first.page.locator(
      '[aria-label^="search-provider-"] input[type="checkbox"]',
    );
    for (let index = 0; index < (await providerCheckboxes.count()); index += 1) {
      const checkbox = providerCheckboxes.nth(index);
      if (await checkbox.isChecked()) await checkbox.uncheck();
    }
    await localProviderCheckbox.check();
    const openAlexProviderCheckbox = first.page
      .getByLabel("search-provider-openalex")
      .locator('input[type="checkbox"]');
    await expect(openAlexProviderCheckbox).toBeEnabled();
    await openAlexProviderCheckbox.check();

    await first.page.getByRole("button", { name: /^(按我的想法|From my idea)$/ }).click();
    const searchSeed = first.page.locator("textarea").first();
    await searchSeed.fill(
      "Graph neural networks and multi-agent language models for molecular drug discovery.",
    );
    await first.page
      .locator("label")
      .filter({ hasText: /^(用 LLM 扩展检索式|Expand queries with the LLM)$/ })
      .locator('input[type="checkbox"]')
      .uncheck();
    await first.page
      .getByRole("button", { name: /^(预览检索式|Preview queries)$/ })
      .click();
    await expect(first.page.getByText("(rules)", { exact: true })).toBeVisible();
    await first.page.getByRole("button", { name: /^(开始检索|Search)$/ }).click();
    await expect(first.page.getByRole("heading", { name: /^(检索结果|Results)/ })).toBeVisible({
      timeout: 30_000,
    });
    await expect(first.page.getByText(/^local \d+$/)).toBeVisible();
    await expect(
      first.page.getByText("Graph Neural Networks for Molecular Property Prediction", {
        exact: true,
      }),
    ).toBeVisible();
    const providerDiagnostics = first.page.getByLabel(/^(检索源诊断|Provider diagnostics)$/);
    await expect(providerDiagnostics).toBeVisible();
    await expect(providerDiagnostics).toContainText("openalex");
    await expect(providerDiagnostics).toContainText(/触发限流|rate limited/);
    await expect(providerDiagnostics).toContainText("HTTP 429");
    await expect(providerDiagnostics).toContainText(/可重试|retryable/);
    await expect(providerDiagnostics).toContainText(/仅重试这个来源|retry this source/);
    expect(mockOpenAlex.requests).toHaveLength(4);

    await providerDiagnostics
      .getByRole("button", { name: /^(仅重试 1 个可恢复来源|Retry 1 recoverable source\(s\))$/ })
      .click();
    await expect(providerDiagnostics).toHaveCount(0, { timeout: 30_000 });
    await expect(first.page.getByText(/^openalex 1$/)).toBeVisible();
    expect(mockOpenAlex.requests.length).toBeGreaterThanOrEqual(5);

    const failureHistory = await first.page.evaluate(async ({ origin, id }) => {
      const response = await fetch(`${origin}/api/search/history?project_id=${id}`);
      return (await response.json()) as {
        items: {
          providers: string[];
          result_count: number;
          params: Record<string, unknown>;
          provider_stats: Record<string, { outcome: string; retryable: boolean }>;
        }[];
      };
    }, { origin: info.backendOrigin, id: projectId });
    expect(failureHistory.items).toHaveLength(2);
    const failedExecution = failureHistory.items.find(
      (entry) => entry.provider_stats.openalex?.outcome === "rate_limited",
    );
    expect(failedExecution?.result_count).toBeGreaterThan(0);
    expect(failedExecution?.provider_stats.openalex.retryable).toBe(true);
    const recoveryExecution = failureHistory.items.find(
      (entry) => entry.providers.length === 1 && entry.providers[0] === "openalex",
    );
    expect(recoveryExecution?.params.use_cache).toBe(false);
    expect(recoveryExecution?.provider_stats.openalex.outcome).toBe("success");

    await first.page.getByRole("button", { name: /^(按已有论文|From a paper)$/ }).click();
    const existingPaper = first.page.getByLabel(/^(已有论文|Existing paper)$/);
    await expect(
      existingPaper.locator("option", {
        hasText: "Efficient Retrieval Augmented Language Models",
      }),
    ).toHaveCount(1);
    await existingPaper.selectOption({ label: "Efficient Retrieval Augmented Language Models" });
    await expect(searchSeed).toHaveValue(
      /Retrieval augmented generation grounds language models/,
    );
    await first.page.getByRole("button", { name: /^(开始检索|Search)$/ }).click();
    await expect.poll(async () => {
      return first.page.evaluate(async ({ origin, id }) => {
        const response = await fetch(`${origin}/api/search/history?project_id=${id}`);
        const payload = (await response.json()) as { items: unknown[] };
        return payload.items.length;
      }, { origin: info.backendOrigin, id: projectId });
    }, { message: "failed, recovery and existing-paper searches should all be persisted" }).toBe(3);
    await expect(
      first.page
        .getByRole("table", { name: /^(检索结果|Search results)$/ })
        .getByText("Efficient Retrieval Augmented Language Models", { exact: true }),
    ).toBeVisible();

    const searchHistory = await first.page.evaluate(async ({ origin, id }) => {
      const response = await fetch(`${origin}/api/search/history?project_id=${id}`);
      return (await response.json()) as {
        items: { mode: string; result_count: number; params: Record<string, unknown> }[];
      };
    }, { origin: info.backendOrigin, id: projectId });
    expect(searchHistory.items.map((entry) => entry.mode).sort()).toEqual(["idea", "idea", "paper"]);
    expect(searchHistory.items.every((entry) => entry.result_count > 0)).toBe(true);
    expect(
      searchHistory.items.every((entry) => entry.params.use_llm_expansion === false),
    ).toBe(true);
    const searchHistoryTable = first.page.getByRole("table", {
      name: /^(检索历史|Search history)$/,
    });
    await expect(searchHistoryTable.locator("tbody tr")).toHaveCount(3);
    await expect(searchHistoryTable).toContainText("idea");
    await expect(searchHistoryTable).toContainText("paper");
    await expect(searchHistoryTable).toContainText(/规则扩展|rule expansion/);
    await expect(searchHistoryTable).toContainText(/1 个来源失败|1 source failure/);
    expect(mockLlm.requests).toHaveLength(0);

    await first.page.getByRole("button", { name: /^(文献库|Library)$/ }).click();
    await expect(first.page.getByText(/12 显示\s*\/\s*12 匹配/)).toBeVisible();

    await first.page.getByRole("button", { name: /^(研究图谱|Landscape)$/ }).click();
    await expect(first.page.getByText("将使用项目中的 12 篇论文。", { exact: true })).toBeVisible();
    await first.page
      .getByRole("button", { name: /^(可增量（Hashing \+ PCA）|Incremental \(Hashing \+ PCA\))$/ })
      .click();
    await expect(first.page.locator(".landscape")).toBeVisible({ timeout: 90_000 });
    await expect(first.page.locator(".canvas-wrap canvas")).toBeVisible();
    await expect(first.page.locator(".overlay.bl")).toContainText("12 篇");
    await expect(first.page.locator(".overlay.bl")).toContainText("hashing:256");

    const ideaTitleForMap = "Graph language models for molecular discovery";
    const ideaAbstractForMap =
      "A multi-agent language model that reasons over molecular graphs for drug discovery.";
    const inspector = first.page.locator(".inspector");
    await inspector.locator("input").first().fill(ideaTitleForMap);
    await inspector.locator("textarea").fill(ideaAbstractForMap);
    await inspector.getByRole("button", { name: "定位", exact: true }).click();
    await expect(inspector.getByRole("heading", { name: "定位结果", exact: true })).toBeVisible();
    await expect(inspector.getByText("精确投影", { exact: true })).toBeVisible();
    await expect(inspector.getByText(/Graph Neural Networks for Molecular Property Prediction/)).toBeVisible();
    await expect(first.page.locator(".overlay.bl")).toContainText("13 篇");
    await expect(first.page.getByText("我的想法 / 论文", { exact: true })).toBeVisible();

    await inspector.getByRole("button", { name: "从图谱中移除", exact: true }).click();
    await expect(first.page.getByText(/^(已从本图谱移除|Removed from this landscape)$/)).toBeVisible();
    await expect(first.page.locator(".overlay.bl")).toContainText("12 篇");
    await inspector.locator("input").first().fill(ideaTitleForMap);
    await inspector.locator("textarea").fill(ideaAbstractForMap);
    await inspector.getByRole("button", { name: "定位", exact: true }).click();
    await expect(inspector.getByText("精确投影", { exact: true })).toBeVisible();
    await expect(first.page.locator(".overlay.bl")).toContainText("13 篇");

    await first.page.getByRole("button", { name: /^(手稿|Manuscript)$/ }).click();
    await expect(first.page.locator(".editor-wrap")).toBeVisible();
    const editor = first.page.locator(".editor-pane .cm-content").first();
    await editor.fill(manuscriptText);
    await expect(first.page.getByRole("button", { name: /^(保存|Save)/ })).toBeEnabled();
    await first.page.getByRole("button", { name: /^(保存|Save)/ }).click();
    await expect(first.page.getByRole("button", { name: /^(保存|Save)/ })).toBeDisabled();

    await expect.poll(async () => {
      const files = (await fs.readdir(manuscriptDirectory)).filter(
        (name) => /^\d+-.+\.md$/i.test(name),
      );
      const contents = await Promise.all(
        files.map((name) => fs.readFile(path.join(manuscriptDirectory, name), "utf8")),
      );
      return contents.some((content) =>
        content.replace(/\r\n/g, "\n").includes(manuscriptText),
      );
    }, { message: "saving in CodeMirror should flush the numbered section file" }).toBe(true);
    const manualSectionFiles = (await fs.readdir(manuscriptDirectory)).filter(
      (name) => /^\d+-.+\.md$/i.test(name),
    );
    const manualSectionName = (
      await Promise.all(
        manualSectionFiles.map(async (name) => ({
          name,
          content: await fs.readFile(path.join(manuscriptDirectory, name), "utf8"),
        })),
      )
    ).find(({ content }) => content.replace(/\r\n/g, "\n").includes(manuscriptText))?.name;
    expect(manualSectionName).toBeTruthy();
    const manualSectionPath = path.join(manuscriptDirectory, manualSectionName!);
    expect(consoleErrors).toEqual([]);

    await first.page.getByRole("button", { name: /^(智能体|Agents)$/ }).click();
    await expect(
      first.page.getByRole("heading", { name: /^(写作智能体|Writing agents)$/ }),
    ).toBeVisible();
    const launcher = first.page.locator(".card").filter({
      has: first.page.getByRole("button", { name: /^(开始运行|Start run)$/ }),
    });
    await launcher
      .getByRole("button", { name: /^(撰写指定章节|Write specific sections)$/ })
      .click();
    await launcher.getByRole("button", { name: /^(引言|Introduction)$/ }).click();
    await launcher
      .locator(".field")
      .filter({ hasText: /^(精读篇数|Papers to read)/ })
      .locator('input[type="number"]')
      .fill("1");
    await launcher
      .locator("label")
      .filter({ hasText: /^(审阅并修订|Review and revise)$/ })
      .locator('input[type="checkbox"]')
      .uncheck();

    await launcher.getByRole("button", { name: /^(预览提示词|Preview prompt)$/ }).click();
    const promptPreview = first.page.locator(".modal");
    await expect(promptPreview.getByText(/^(提示词预览|Prompt preview)/)).toBeVisible();
    await expect(promptPreview).toContainText(
      "Graph Neural Networks for Molecular Property Prediction",
    );
    await expect(promptPreview.getByText(/^skill:/).first()).toBeVisible();
    await promptPreview.locator("header button").click();
    await expect(promptPreview).toBeHidden();

    await launcher.getByRole("button", { name: /^(开始运行|Start run)$/ }).click();
    const completedRunHeading = first.page.getByRole("heading", {
      name: /^(撰写指定章节 · 已完成|section · done)$/,
    });
    await expect(completedRunHeading).toBeVisible({ timeout: 90_000 });
    const runCard = first.page.locator(".card").filter({ has: completedRunHeading });
    await expect(runCard.getByText(/^(1 节已写入|1 sections written)$/)).toBeVisible();
    const completedSteps = runCard.getByRole("table", {
      name: /^(智能体步骤|Agent steps)$/,
    });
    await expect(completedSteps.locator("tbody tr")).toHaveCount(4);
    await expect(runCard).toContainText("Read the key papers");
    await expect(runCard).toContainText("Synthesise the literature");
    await expect(runCard).toContainText("Translate: introduction");
    const writerRow = completedSteps.locator("tbody tr").filter({ hasText: "Draft: introduction" });
    await expect(writerRow).toBeVisible();
    await writerRow.getByRole("button", { name: /^(审查|Audit)$/ }).click();
    const auditModal = first.page.locator(".modal");
    await expect(auditModal.getByText(/^(发送的提示词|Prompt sent)$/)).toBeVisible();
    await expect(auditModal).toContainText("SECTION TO WRITE: Introduction");
    await expect(auditModal).toContainText("## Active skills");
    await expect(auditModal).toContainText(agentDraftMarker);
    await auditModal.locator("header button").click();
    await expect(auditModal).toBeHidden();

    const qualityPanel = runCard.getByRole("region", {
      name: /^(论文质量报告|Manuscript quality report)$/,
    });
    await expect(qualityPanel).toBeVisible();
    await expect(qualityPanel).toContainText(/自动门禁：需注意|Automatic gate: warn/);
    await expect(
      qualityPanel.getByRole("table", { name: /^(自动质量检查|Automatic quality checks)$/ }),
    ).toContainText("citation key integrity");
    await expect(qualityPanel).toContainText(/0 个无效键|0 invalid keys/);
    await expect(qualityPanel).toContainText(/冻结正文证据|Frozen manuscript evidence/);
    await expect(qualityPanel).toContainText(agentDraftMarker);

    await qualityPanel
      .getByRole("button", { name: /^(进入盲评|Enter blind review)$/ })
      .click();
    await expect(
      qualityPanel.getByRole("heading", { name: /盲评样本|Blind review sample/ }),
    ).toBeVisible();
    await expect(qualityPanel).not.toContainText("E2E Research Lead");
    await qualityPanel
      .getByRole("button", { name: /^(导出盲评包|Export blind packet)$/ })
      .click();
    await expect(
      first.page.locator(".toast.success").filter({
        hasText: /评审证据包已导出|Review evidence packet exported/,
      }),
    ).toBeVisible();
    const reviewExportDirectory = path.join(projectRoot, "exports", "reviews");
    await expect.poll(async () => {
      try {
        return (await fs.readdir(reviewExportDirectory)).some((name) =>
          /-blind\.json$/i.test(name),
        );
      } catch {
        return false;
      }
    }).toBe(true);
    const blindExportName = (await fs.readdir(reviewExportDirectory)).find((name) =>
      /-blind\.json$/i.test(name),
    );
    expect(blindExportName).toBeTruthy();
    const blindExport = JSON.parse(
      await fs.readFile(path.join(reviewExportDirectory, blindExportName!), "utf8"),
    ) as Record<string, unknown>;
    const blindExportText = JSON.stringify(blindExport);
    expect(blindExport.identity_hidden).toBe(true);
    expect(blindExport).not.toHaveProperty("provenance");
    expect(blindExport).not.toHaveProperty("human_evaluations");
    expect(blindExportText).not.toContain(projectId);
    expect(blindExportText).not.toContain(projectRoot);
    expect(blindExportText).not.toContain("pdf_path");

    await qualityPanel.getByLabel(/^(已核对章节|Reviewed section) /).check();
    await qualityPanel.getByLabel(/^(已核对来源|Reviewed source) /).check();
    await qualityPanel.getByLabel(/^(评审结论|Decision)$/).selectOption("accepted");
    await qualityPanel
      .getByText(
        /我已打开并核对引用来源，而不是只阅读模型生成的摘要|I opened and checked the cited sources/,
      )
      .click();
    await qualityPanel
      .getByText(
        /我已阅读自动警告，并在证据说明中记录处理结论|I reviewed the automatic warnings/,
      )
      .click();
    await qualityPanel
      .getByLabel(/匿名评审编号|Anonymous reviewer code/)
      .fill("E2E Blind Reviewer B");
    await qualityPanel
      .getByLabel(/^(证据与修订说明|Evidence and revision notes)$/)
      .fill("Blindly inspected the frozen prose and checked the cited claim against its source.");
    await qualityPanel
      .getByRole("button", { name: /^(保存人工评审|Save human review)$/ })
      .click();
    await expect(
      first.page.locator(".toast.success").filter({
        hasText: /人工质量评审已保存|Human quality review saved/,
      }),
    ).toBeVisible();

    await expect(
      qualityPanel.getByRole("button", { name: /^(进入盲评|Enter blind review)$/ }),
    ).toBeVisible();
    await expect(qualityPanel).toContainText("E2E Blind Reviewer B");
    await expect(qualityPanel).toContainText("blind");
    await qualityPanel.getByLabel(/^(评审人|Reviewer)$/).fill("E2E Research Lead");
    await qualityPanel
      .getByLabel(/^(证据与修订说明|Evidence and revision notes)$/)
      .fill("Independently opened the managed source and checked the cited claim.");
    await qualityPanel
      .getByRole("button", { name: /^(保存人工评审|Save human review)$/ })
      .click();
    await expect(
      first.page.locator(".toast.success").filter({
        hasText: /人工质量评审已保存|Human quality review saved/,
      }).last(),
    ).toBeVisible();
    await expect(qualityPanel).toContainText("E2E Research Lead");
    await expect(qualityPanel).toContainText("accepted");
    const evaluationSummary = first.page.getByRole("region", {
      name: /^(人工质量评审摘要|Human quality review summary)$/,
    });
    await expect(evaluationSummary).toContainText(/1 个已评运行|1 reviewed runs/);
    await expect(evaluationSummary).toContainText(/独立复评一致性|Independent review agreement/);
    await expect(evaluationSummary).toContainText(/2 位评审人|2 reviewers/);

    expect(mockLlm.requests).toHaveLength(4);
    expect(mockLlm.requests.map((request) => request.stream)).toEqual([
      false,
      false,
      true,
      true,
    ]);
    expect(
      mockLlm.requests.every(
        (request) => request.authorization === "Bearer papercreator-e2e-local-only",
      ),
    ).toBe(true);
    expect(mockLlm.requests[2].messages[0]?.content).toContain("## Active skills");
    await expect.poll(async () => {
      const files = (await fs.readdir(manuscriptDirectory)).filter(
        (name) => /^\d+-introduction\.md$/i.test(name),
      );
      const content =
        files.length === 1
          ? await fs.readFile(path.join(manuscriptDirectory, files[0]), "utf8")
          : "";
      return files.length === 1
        ? content.includes(agentDraftMarker) && content.includes(agentTranslationMarker)
        : false;
    }, { message: "the bilingual Agent run should flush both languages to disk" }).toBe(true);

    // Interrupt the next real streaming response after its first delta. The
    // failed run must keep the previous manuscript, expose a retry/snapshot
    // recovery path, and persist the partial delta for audit.
    mockLlm.failNextStream();
    await launcher.getByRole("button", { name: /^(开始运行|Start run)$/ }).click();
    const failedRunHeading = first.page.getByRole("heading", {
      name: /^(撰写指定章节 · 失败|section · failed)$/,
    });
    await expect(failedRunHeading).toBeVisible({ timeout: 90_000 });
    const failedRunCard = first.page.locator(".card").filter({ has: failedRunHeading });
    await expect(failedRunCard).toContainText("stream interrupted");
    await expect(failedRunCard).toContainText(/运行前恢复点|Pre-run recovery point/);
    await expect(
      failedRunCard.getByRole("button", { name: /^(重试相同运行|Retry the same run)$/ }),
    ).toBeVisible();
    await expect(
      failedRunCard.getByRole("button", { name: /^(比较或恢复快照|Compare or restore snapshot)$/ }),
    ).toBeVisible();
    const agentFailureToast = first.page.locator(".toast.error").filter({ hasText: /Agent .*stream interrupted/ });
    await expect(agentFailureToast).toBeVisible();
    await agentFailureToast.getByRole("button", { name: /^(关闭通知|Dismiss)$/ }).click();
    await expect(agentFailureToast).toBeHidden();

    const failedWriterRow = failedRunCard.getByRole("table", {
      name: /^(智能体步骤|Agent steps)$/,
    }).locator("tbody tr").filter({
      hasText: "Draft: introduction",
    });
    await expect(failedWriterRow).toContainText("stream_interrupted");
    await failedWriterRow.getByRole("button", { name: /^(审查|Audit)$/ }).click();
    const failedAudit = first.page.locator(".modal");
    await expect(failedAudit).toContainText(
      "Molecular discovery increasingly depends on representations",
    );
    await failedAudit.locator("header button").click();
    await expect(failedAudit).toBeHidden();

    const durableFailure = await first.page.evaluate(async ({ origin, id }) => {
      const listResponse = await fetch(`${origin}/api/agents/runs?project_id=${id}`);
      const list = (await listResponse.json()) as {
        items: Array<{ id: string; status: string }>;
      };
      const failed = list.items.find((item) => item.status === "failed");
      if (!failed) return null;
      const response = await fetch(`${origin}/api/agents/runs/${failed.id}`);
      return response.json();
    }, { origin: info.backendOrigin, id: projectId }) as {
      id: string;
      status: string;
      result: {
        failure: { outcome: string; retryable: boolean };
        recovery: { strategy: string; restore_snapshot_id: string };
        snapshots: { before: string; after: string };
      };
      steps: Array<{ agent: string; status: string; output: string }>;
    } | null;
    expect(durableFailure?.status).toBe("failed");
    expect(durableFailure?.result.failure).toMatchObject({
      outcome: "stream_interrupted",
      retryable: true,
    });
    expect(durableFailure?.result.recovery.strategy).toBe("partial_work_preserved");
    expect(durableFailure?.result.snapshots.before).toBeTruthy();
    expect(durableFailure?.result.snapshots.after).toBeTruthy();
    expect(
      durableFailure?.steps.find((step) => step.agent === "writer"),
    ).toMatchObject({ status: "failed" });
    expect(
      durableFailure?.steps.find((step) => step.agent === "writer")?.output,
    ).toContain("Molecular discovery increasingly depends");
    expect(mockLlm.requests).toHaveLength(8);
    await expect.poll(async () => {
      const files = (await fs.readdir(manuscriptDirectory)).filter(
        (name) => /^\d+-introduction\.md$/i.test(name),
      );
      const content = files.length === 1
        ? await fs.readFile(path.join(manuscriptDirectory, files[0]), "utf8")
        : "";
      return content.includes(agentDraftMarker) && content.includes(agentTranslationMarker);
    }, { message: "a failed redraft must not corrupt the last complete manuscript" }).toBe(true);

    await assistantPanel.getByRole("button", { name: /^(发送|Send)$/ }).click();
    await expect(assistantPanel.getByText(/^\{"ok":\s*true\}$/)).toBeVisible();
    await expect(assistantPanel.getByRole("combobox", { name: /^(对话历史|Conversation history)$/ })).not.toHaveValue("");
    const durableAssistantThread = await assistantPanel.getByRole("combobox", {
      name: /^(对话历史|Conversation history)$/,
    }).inputValue();
    const storedAssistantMessages = await first.page.evaluate(async ({ origin, thread }) => {
      const response = await fetch(`${origin}/api/assistant/threads/${thread}`);
      return response.json() as Promise<{ messages: Array<{ role: string; content: string }> }>;
    }, { origin: info.backendOrigin, thread: durableAssistantThread });
    expect(storedAssistantMessages.messages.map((message) => message.role)).toEqual(["user", "assistant"]);
    expect(storedAssistantMessages.messages[1].content).toMatch(/^\{"ok":\s*true\}$/);
    await assistantPanel.getByTitle(/^(管理对话数据|Manage conversation data)$/).click();
    modal = first.page.locator(".modal");
    await expect(modal.getByText(/^(AI 对话数据管理|AI conversation data)$/)).toBeVisible();
    await expect(modal.locator(".assistant-governance-stats dd").nth(0)).toHaveText("1");
    await expect(modal.locator(".assistant-governance-stats dd").nth(1)).toHaveText("2");
    await modal.getByRole("button", { name: /^(导出范围内全部对话|Export all in scope)$/ }).click();
    const assistantExportPath = path.join(workbench, "e2e-assistant-conversations.json");
    await expect.poll(async () => {
      try {
        return JSON.parse(await fs.readFile(assistantExportPath, "utf8")) as {
          format: string;
          scope: { project_id: string };
          threads: Array<{ id: string; messages: Array<{ role: string }> }>;
        };
      } catch {
        return null;
      }
    }, { message: "assistant conversation export should be saved through Electron" }).not.toBeNull();
    const exportedAssistantData = JSON.parse(await fs.readFile(assistantExportPath, "utf8")) as {
      format: string;
      scope: { project_id: string };
      threads: Array<{ id: string; messages: Array<{ role: string }> }>;
    };
    expect(exportedAssistantData.format).toBe("papercreator.assistant_conversations");
    expect(exportedAssistantData.scope.project_id).toBe(projectId);
    expect(exportedAssistantData.threads).toHaveLength(1);
    expect(exportedAssistantData.threads[0].id).toBe(durableAssistantThread);
    expect(exportedAssistantData.threads[0].messages.map((message) => message.role)).toEqual(["user", "assistant"]);
    await modal.getByRole("button", { name: /^(导出压缩归档|Export compressed archive)$/ }).click();
    const assistantArchivePath = path.join(workbench, "e2e-assistant-conversations.json.gz");
    await expect.poll(async () => {
      try {
        return JSON.parse(gunzipSync(await fs.readFile(assistantArchivePath)).toString("utf8")) as {
          format: string;
          threads: unknown[];
        };
      } catch {
        return null;
      }
    }, { message: "compressed assistant archive should be saved through Electron" }).toMatchObject({
      format: "papercreator.assistant_conversations",
      threads: [{ id: durableAssistantThread }],
    });
    await expect(modal.getByRole("button", { name: /^(在文件夹中显示|Show in folder)$/ })).toBeVisible();
    await modal.getByRole("checkbox", { name: /^(启用按最后活动时间清理|Enable cleanup by last activity)$/ }).check();
    await modal.getByLabel(/^(保留最近天数|Keep recent days)$/).fill("30");
    await modal.getByRole("button", { name: /^(保存保留策略|Save retention policy)$/ }).click();
    await expect(modal.getByRole("button", { name: /^(预览到期清理|Preview expired cleanup)$/ })).toBeEnabled();
    await modal.getByRole("button", { name: /^(关闭对话数据管理|Close conversation data)$/ }).click();
    await expect(modal).toBeHidden();
    await expect(assistantPanel.getByRole("button", { name: /^(打开文献检索|Open literature search)$/ })).toBeVisible();
    await expect(assistantPanel.getByRole("button", { name: /^(追加到当前章节|Append to current section)$/ })).toBeVisible();
    await assistantPanel.getByRole("button", { name: /^(提交本地版本|Commit local version)$/ }).click();
    modal = first.page.locator(".modal");
    await expect(modal.getByLabel(/^(提交说明|Commit message)$/)).toHaveValue("PaperCreator assistant checkpoint");
    const localCommitButton = modal.getByRole("button", { name: /^(创建本地提交|Create local commit)$/ });
    await expect(localCommitButton).toBeDisabled();
    await modal.getByRole("checkbox").check();
    await expect(localCommitButton).toBeEnabled();
    await modal.getByRole("button", { name: /^(取消|Cancel)$/ }).click();
    await expect(modal).toBeHidden();
    expect(mockLlm.requests).toHaveLength(9);
    expect(mockLlm.requests[8].messages[0]?.content).toContain("PaperCreator");

    // Bulk translation must finish as a durable preview before one confirmed
    // write updates every selected paired section.
    await first.page.getByRole("button", { name: /^(手稿|Manuscript)$/ }).click();
    const manualSectionKey = manualSectionName!.replace(/^\d+-/, "").replace(/\.md$/i, "");
    const pairedBeforeTranslation = await first.page.evaluate(async ({ origin, project, key }) => {
      const response = await fetch(`${origin}/api/writing/${project}/sections/${encodeURIComponent(key)}`);
      return (await response.json()) as { content_zh: string };
    }, { origin: info.backendOrigin, project: projectId, key: manualSectionKey });
    expect(pairedBeforeTranslation.content_zh).toBe("");
    await first.page.getByRole("button", { name: /^(翻译全部|Translate all)$/ }).click();
    modal = first.page.locator(".modal");
    await modal.getByRole("button", { name: /^(生成完整预览|Generate complete preview)$/ }).click();
    await expect(modal.getByText(/^(完整译文预览（尚未写入）|Complete translation preview \(not applied\))$/)).toBeVisible({ timeout: 30_000 });
    const pairedDuringPreview = await first.page.evaluate(async ({ origin, project, key }) => {
      const response = await fetch(`${origin}/api/writing/${project}/sections/${encodeURIComponent(key)}`);
      return (await response.json()) as { content_zh: string };
    }, { origin: info.backendOrigin, project: projectId, key: manualSectionKey });
    expect(pairedDuringPreview.content_zh).toBe("");
    await modal.getByRole("checkbox", { name: /^(我已检查译文，确认一次性写入上述章节|I reviewed the translations and confirm applying all sections at once)$/ }).check();
    await modal.getByRole("button", { name: /^(确认并一次性写入|Confirm and apply once)$/ }).click();
    await expect(modal).toBeHidden();
    await expect.poll(async () => {
      const paired = await first.page.evaluate(async ({ origin, project, key }) => {
        const response = await fetch(`${origin}/api/writing/${project}/sections/${encodeURIComponent(key)}`);
        return (await response.json()) as { content_zh: string };
      }, { origin: info.backendOrigin, project: projectId, key: manualSectionKey });
      return paired.content_zh;
    }, { message: "confirmed translation preview should apply in one backend write" }).toMatch(/^\{"ok":\s*true\}$/);

    // A normal save must refuse to overwrite a file changed outside
    // PaperCreator. Both explicit recovery directions are then exercised:
    // database -> files keeps a disk backup; files -> database keeps a snapshot.
    await first.page.getByRole("button", { name: /^(手稿|Manuscript)$/ }).click();
    const conflictEditor = first.page.locator(".editor-pane .cm-content").first();
    await expect(conflictEditor).toContainText("Reproducible E2E manuscript");

    const otherSectionName = manualSectionFiles.find((name) => name !== manualSectionName);
    expect(otherSectionName).toBeTruthy();
    const expectedConflictConsole =
      "Failed to load resource: the server responded with a status of 409 (Conflict)";
    const otherSectionKey = otherSectionName!.replace(/^\d+-/, "").replace(/\.md$/i, "");
    const otherSectionPath = path.join(manuscriptDirectory, otherSectionName!);
    await fs.writeFile(
      manualSectionPath,
      `# Abstract\n\n${disjointDiskMarker}\n`,
      "utf8",
    );
    const disjointUpdateStatus = await first.page.evaluate(async ({ origin, project, key, content }) => {
      const response = await fetch(
        `${origin}/api/writing/${project}/sections/${encodeURIComponent(key)}`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content }),
        },
      );
      return response.status;
    }, {
      origin: info.backendOrigin,
      project: projectId,
      key: otherSectionKey,
      content: disjointDatabaseMarker,
    });
    expect(disjointUpdateStatus).toBe(409);
    await expect.poll(
      () => consoleErrors.filter((message) => message === expectedConflictConsole).length,
      { message: "the disjoint update should produce exactly one handled HTTP 409" },
    ).toBe(1);
    consoleErrors.splice(consoleErrors.indexOf(expectedConflictConsole), 1);
    const disjointBanner = first.page.locator(".guidance").filter({ hasText: "diverged" });
    await expect(disjointBanner).toBeVisible({ timeout: 20_000 });
    await expect(disjointBanner).toContainText(otherSectionKey);
    await expect(disjointBanner).toContainText(manualSectionKey);
    first.page.once("dialog", (dialog) => dialog.accept());
    await disjointBanner.getByRole("button", {
      name: /^(合并不同章节|Merge disjoint sections)$/,
    }).click();
    await expect(first.page.getByText(
      /^(不同章节的数据库与磁盘修改已安全合并|Disjoint database and disk section changes were merged safely)$/,
    )).toBeVisible();
    await expect(disjointBanner).toBeHidden();
    await expect(conflictEditor).toContainText(disjointDiskMarker);
    await expect.poll(async () => (await fs.readFile(otherSectionPath, "utf8")).includes(
      disjointDatabaseMarker,
    )).toBe(true);

    // Restore the current section before exercising a true overlapping conflict.
    await conflictEditor.fill(manuscriptText);
    await first.page.getByRole("button", { name: /^(保存|Save)/ }).click();
    await expect(first.page.getByRole("button", { name: /^(保存|Save)/ })).toBeDisabled();

    await fs.writeFile(
      manualSectionPath,
      `# Abstract\n\n${externalDiskMarker}\n`,
      "utf8",
    );
    await expect(
      first.page.locator(".guidance").filter({ hasText: "disk_changed" }),
    ).toBeVisible({ timeout: 20_000 });

    await conflictEditor.fill(databasePreferredMarker);
    await first.page.getByRole("button", { name: /^(保存|Save)/ }).click();
    const conflictError = first.page
      .locator(".toast.error")
      .filter({ hasText: "manuscript sync conflict" });
    await expect(conflictError).toBeVisible();
    await expect.poll(
      () => consoleErrors.filter((message) => message === expectedConflictConsole).length,
      { message: "the protected save should produce exactly one handled HTTP 409" },
    ).toBe(1);
    consoleErrors.splice(consoleErrors.indexOf(expectedConflictConsole), 1);
    await expect(
      first.page.locator(".guidance").filter({ hasText: "diverged" }),
    ).toBeVisible({ timeout: 20_000 });
    await conflictError.getByRole("button", { name: /^(关闭通知|Dismiss)$/ }).click();

    first.page.once("dialog", (dialog) => dialog.accept());
    await first.page
      .locator(".guidance")
      .filter({ hasText: "diverged" })
      .getByRole("button", { name: /^(以数据库为准|Use database)$/ })
      .click();
    await expect(
      first.page.getByText(/^(已以数据库为准完成同步|Synchronized from the database)$/),
    ).toBeVisible();
    await expect(first.page.locator(".guidance").filter({ hasText: "diverged" })).toBeHidden();
    await expect(conflictEditor).toContainText(databasePreferredMarker);
    await expect(first.page.getByRole("button", { name: /^(保存|Save)/ })).toBeDisabled();
    await expect.poll(async () => {
      const content = await fs.readFile(manualSectionPath, "utf8");
      return content.includes(databasePreferredMarker) && !content.includes(externalDiskMarker);
    }, { message: "database resolution should flush the accepted DB text" }).toBe(true);

    const conflictDirectory = path.join(
      managed,
      "projects",
      projectDirectory,
      ".papercreator",
      "conflicts",
    );
    await expect.poll(async () => {
      const backups = await fs.readdir(conflictDirectory, { withFileTypes: true }).catch(() => []);
      for (const backup of backups) {
        if (!backup.isDirectory()) continue;
        const candidate = path.join(
          conflictDirectory,
          backup.name,
          "disk",
          path.basename(manualSectionPath),
        );
        const content = await fs.readFile(candidate, "utf8").catch(() => "");
        if (content.includes(externalDiskMarker)) return true;
      }
      return false;
    }, { message: "database resolution should preserve the overwritten disk file" }).toBe(true);

    await fs.writeFile(
      manualSectionPath,
      `# Abstract\n\n${filePreferredMarker}\n`,
      "utf8",
    );
    const diskChangedBanner = first.page.locator(".guidance").filter({ hasText: "disk_changed" });
    await expect(diskChangedBanner).toBeVisible({ timeout: 20_000 });
    first.page.once("dialog", (dialog) => dialog.accept());
    await diskChangedBanner
      .getByRole("button", { name: /^(以文件为准|Use files)$/ })
      .click();
    await expect(
      first.page.getByText(/^(已以磁盘文件为准完成同步|Synchronized from disk files)$/),
    ).toBeVisible();
    await expect(diskChangedBanner).toBeHidden();
    await expect(conflictEditor).toContainText(filePreferredMarker);
    await expect(first.page.getByRole("button", { name: /^(保存|Save)/ })).toBeDisabled();

    // Restore the stable fixture through the normal editor so the later version
    // and export assertions continue to prove their original contract.
    await conflictEditor.fill(manuscriptText);
    await first.page.getByRole("button", { name: /^(保存|Save)/ }).click();
    await expect(first.page.getByRole("button", { name: /^(保存|Save)/ })).toBeDisabled();

    await first.page.getByRole("button", { name: /^(版本|Versions)$/ }).click();
    await expect(first.page.getByRole("heading", { name: /^(版本历史|Version history)$/ })).toBeVisible();
    await expect(
      first.page.getByText("before resolving manuscript conflict from files", { exact: true }),
    ).toBeVisible();
    // The project was intentionally created without Git so this also verifies
    // the explicit desktop initialisation path before any discard operation.
    await first.page
      .getByRole("button", { name: /^(启用本地 Git|Enable local Git)$/ })
      .click();
    await expect(first.page.getByLabel(/^(Git 分支|Git branches)$/)).toBeVisible();
    await expect(first.page.getByText(/git · main/, { exact: true })).toBeVisible();
    await first.page
      .getByPlaceholder(/^(版本说明|What changed)/)
      .fill(versionLabel);
    await first.page.getByRole("button", { name: /^(本地提交版本|Commit local version)$/ }).click();
    const baselineRows = first.page.locator("tbody tr").filter({ hasText: versionLabel });
    await expect(baselineRows).toHaveCount(2);
    await expect(baselineRows.getByText("git", { exact: true })).toBeVisible();
    const baselineRow = baselineRows.filter({
      has: first.page.getByText("snapshot", { exact: true }),
    });
    await expect(baselineRow).toBeVisible();
    await expect(baselineRow.getByText("snapshot", { exact: true })).toBeVisible();

    // Exercise the real Remote Git desktop surface against a local bare repo.
    // Fetch must not touch files; Pull may only fast-forward a clean tree and
    // must capture recovery material before re-indexing the remote manuscript.
    const remoteRoot = path.join(workbench, "E2E 远程 仓库.git");
    const peerRoot = path.join(workbench, "E2E collaborator clone");
    await fs.mkdir(remoteRoot);
    await git(remoteRoot, ["init", "--bare"]);
    await git(remoteRoot, ["symbolic-ref", "HEAD", "refs/heads/main"]);
    const optionalRemoteCard = first.page.getByLabel(/^(可选远程 Git|Optional Remote Git)$/);
    await expect(optionalRemoteCard).toContainText(/仅保存在本机|stay on this computer/);
    await optionalRemoteCard
      .getByRole("button", { name: /^(添加远程仓库|Add remote repository)$/ })
      .click();
    const remoteCard = first.page.getByLabel(/^(远程 Git|Remote Git)$/);
    await expect(remoteCard).toBeVisible();
    await remoteCard.getByLabel(/^(Git remote 地址|Git remote URL)$/).fill(remoteRoot);
    await remoteCard.getByRole("button", { name: /^(配置|Configure)$/ }).click();
    await expect(first.page.getByText(/^(远程仓库已配置|Remote configured)$/)).toBeVisible();
    await expect(remoteCard).toContainText(remoteRoot);
    const remotePushButton = remoteCard.getByRole("button", { name: "Push", exact: true });
    await remotePushButton.click();
    await expect(remotePushButton).toBeDisabled();
    await expect(remotePushButton).toBeEnabled({ timeout: 20_000 });
    await expect(first.page.getByText(/^(本地提交已推送|Local commits pushed)$/).last()).toBeVisible();
    await expect(remoteCard.getByLabel(/^(远程同步状态|Remote sync status)$/)).toContainText(
      /^(已同步|up to date)/,
    );

    await git(workbench, ["clone", remoteRoot, peerRoot]);
    await git(peerRoot, ["config", "user.name", "E2E Collaborator"]);
    await git(peerRoot, ["config", "user.email", "collaborator@localhost"]);
    const peerSectionPath = path.join(peerRoot, path.relative(projectRoot, manualSectionPath));
    await fs.writeFile(peerSectionPath, `# Abstract\n\n${remoteManuscriptMarker}\n`, "utf8");
    await git(peerRoot, ["add", "--", path.relative(peerRoot, peerSectionPath)]);
    await git(peerRoot, ["commit", "-m", "collaborator updates abstract"]);
    await git(peerRoot, ["push", "origin", "main"]);

    const conflictsBeforePull = new Set(await fs.readdir(conflictDirectory));
    await remoteCard.getByRole("button", { name: "Fetch", exact: true }).click();
    await expect(first.page.getByText(/^(远程状态已刷新|Remote state refreshed)$/)).toBeVisible();
    await expect(remoteCard.getByLabel(/^(远程同步状态|Remote sync status)$/)).toContainText(
      /远端有更新|remote ahead/,
    );
    expect(await fs.readFile(manualSectionPath, "utf8")).not.toContain(remoteManuscriptMarker);

    first.page.once("dialog", (dialog) => {
      expect(dialog.message()).toMatch(/快进更新|fast-forward/);
      return dialog.accept();
    });
    await remoteCard.getByRole("button", { name: "Pull (ff-only)", exact: true }).click();
    await expect(
      first.page.getByText(/^(远程论文已安全快进|Remote work fast-forwarded safely)$/),
    ).toBeVisible();
    await expect(remoteCard.getByLabel(/^(远程同步状态|Remote sync status)$/)).toContainText(
      /^(已同步|up to date)/,
    );
    await first.page.getByRole("button", { name: /^(手稿|Manuscript)$/ }).click();
    const remoteEditor = first.page.locator(".editor-pane .cm-content").first();
    await expect(remoteEditor).toContainText(remoteManuscriptMarker);
    expect(await fs.readFile(manualSectionPath, "utf8")).toContain(remoteManuscriptMarker);
    const pullRecoveryFiles = (await filesBelow(conflictDirectory)).filter(
      (candidate) =>
        !conflictsBeforePull.has(path.relative(conflictDirectory, candidate).split(path.sep)[0]),
    );
    const pullRecoveryContents = await Promise.all(
      pullRecoveryFiles
        .filter((candidate) => candidate.endsWith(".md"))
        .map((candidate) => fs.readFile(candidate, "utf8").catch(() => "")),
    );
    expect(pullRecoveryContents.some((content) => content.includes("Reproducible E2E manuscript"))).toBe(
      true,
    );

    // Return to the stable manuscript through the normal UI, commit it and use
    // the same Remote Git card for a second, now fast-forward, push.
    await remoteEditor.fill(manuscriptText);
    await first.page.getByRole("button", { name: /^(保存|Save)/ }).click();
    await expect(first.page.getByRole("button", { name: /^(保存|Save)/ })).toBeDisabled();
    await first.page.getByRole("button", { name: /^(版本|Versions)$/ }).click();
    await first.page
      .getByPlaceholder(/^(版本说明|What changed)/)
      .fill(postRemoteVersionLabel);
    await first.page.getByRole("button", { name: /^(本地提交版本|Commit local version)$/ }).click();
    await expect(first.page.locator("tbody tr").filter({ hasText: postRemoteVersionLabel })).toHaveCount(2);
    await expect(remoteCard).toBeVisible();
    await remotePushButton.click();
    await expect(remotePushButton).toBeDisabled();
    await expect(remotePushButton).toBeEnabled({ timeout: 20_000 });
    await expect(first.page.getByText(/^(本地提交已推送|Local commits pushed)$/).last()).toBeVisible();

    // Now create one commit on each side of the shared base. The desktop must
    // expose ahead/behind and refuse Pull without starting an automatic merge.
    await git(peerRoot, ["pull", "--ff-only"]);
    const peerDivergencePath = path.join(peerRoot, "peer-divergence-note.md");
    await fs.writeFile(peerDivergencePath, "remote-only divergent work\n", "utf8");
    await git(peerRoot, ["add", "--", path.basename(peerDivergencePath)]);
    await git(peerRoot, ["commit", "-m", "peer divergent work"]);
    await git(peerRoot, ["push", "origin", "main"]);

    const localDivergencePath = path.join(projectRoot, "local-divergence-note.md");
    await fs.writeFile(localDivergencePath, "local-only divergent work\n", "utf8");
    await first.page
      .getByPlaceholder(/^(版本说明|What changed)/)
      .fill(divergentVersionLabel);
    await first.page.getByRole("button", { name: /^(本地提交版本|Commit local version)$/ }).click();
    await expect(first.page.locator("tbody tr").filter({ hasText: divergentVersionLabel })).toHaveCount(2);
    await remoteCard.getByRole("button", { name: "Fetch", exact: true }).click();
    const divergentSync = remoteCard.getByLabel(/^(远程同步状态|Remote sync status)$/);
    await expect(divergentSync).toContainText(/历史已分叉|history diverged/);
    await expect(divergentSync).toContainText("↑ 1");
    await expect(divergentSync).toContainText("↓ 1");

    expectedRemoteConflictConsole = true;
    first.page.once("dialog", (dialog) => dialog.accept());
    await remoteCard.getByRole("button", { name: "Pull (ff-only)", exact: true }).click();
    await expect(first.page.getByText(/local and remote history have diverged/)).toBeVisible();
    await expect.poll(() => expectedRemoteConflictConsole).toBe(false);
    expect(await fs.readFile(localDivergencePath, "utf8")).toContain("local-only");
    await expect.poll(
      () => fs.stat(path.join(projectRoot, "peer-divergence-note.md")).then(() => true).catch(() => false),
      { message: "a refused divergent pull must not copy the peer file into the worktree" },
    ).toBe(false);

    // Disconnecting GitHub/GitLab-style collaboration must leave the local
    // repository, current branch and every commit intact.
    const localHeadBeforeDisconnect = await execFileAsync("git", ["rev-parse", "HEAD"], {
      cwd: projectRoot,
      windowsHide: true,
    });
    first.page.once("dialog", (dialog) => dialog.accept());
    await remoteCard
      .getByRole("button", { name: /^(移除远程|Remove remote)$/ })
      .click();
    await expect(
      first.page.getByText(/^(远程已移除，本地历史已保留|Remote removed; local history preserved)$/),
    ).toBeVisible();
    await expect(optionalRemoteCard).toBeVisible();
    await expect(first.page.getByText(/git · main/, { exact: true })).toBeVisible();
    const localHeadAfterDisconnect = await execFileAsync("git", ["rev-parse", "HEAD"], {
      cwd: projectRoot,
      windowsHide: true,
    });
    expect(localHeadAfterDisconnect.stdout.trim()).toBe(localHeadBeforeDisconnect.stdout.trim());

    const conflictsBeforeDiscard = new Set(await fs.readdir(conflictDirectory));
    const untrackedDirectory = path.join(projectRoot, "notes");
    const untrackedPath = path.join(untrackedDirectory, "e2e-untracked-note.txt");
    await fs.mkdir(untrackedDirectory, { recursive: true });
    await fs.writeFile(
      untrackedPath,
      "This untracked note must survive a tracked-file discard.",
      "utf8",
    );

    await first.page.getByRole("button", { name: /^(手稿|Manuscript)$/ }).click();
    const revisedEditor = first.page.locator(".editor-pane .cm-content").first();
    await revisedEditor.fill(revisedText);
    await first.page.getByRole("button", { name: /^(保存|Save)/ }).click();
    await expect(first.page.getByRole("button", { name: /^(保存|Save)/ })).toBeDisabled();

    await first.page.getByRole("button", { name: /^(版本|Versions)$/ }).click();
    await expect(baselineRow).toBeVisible();
    await baselineRow
      .getByRole("button", { name: /^(与当前对比|Diff vs current)$/ })
      .click();
    const diffModal = first.page.locator(".modal");
    await expect(diffModal.getByText("Temporary revision", { exact: false })).toBeVisible();
    await diffModal.locator("header button").click();
    await expect(diffModal).toBeHidden();

    const uncommittedPanel = first.page.getByLabel(/^(未提交的修改|Uncommitted changes)$/);
    await expect(uncommittedPanel).toContainText(/已跟踪文件有修改|tracked file\(s\) changed/);
    await expect(uncommittedPanel).toContainText("notes/e2e-untracked-note.txt");
    first.page.once("dialog", (dialog) => {
      expect(dialog.message()).toContain(".papercreator/conflicts/");
      expect(dialog.message()).toContain("e2e-untracked-note.txt");
      return dialog.accept();
    });
    await uncommittedPanel
      .getByRole("button", { name: /^(放弃已跟踪修改|Discard tracked changes)$/ })
      .click();
    await expect(
      first.page.getByText(/^(已安全放弃已跟踪修改|Tracked changes safely discarded)$/),
    ).toBeVisible();
    await expect(uncommittedPanel).toContainText(/0 个已跟踪文件|0 tracked file/);
    await expect(uncommittedPanel).toContainText("notes/e2e-untracked-note.txt");
    expect(await fs.readFile(untrackedPath, "utf8")).toContain("must survive");

    await first.page.getByRole("button", { name: /^(手稿|Manuscript)$/ }).click();
    await expect(first.page.locator(".editor-pane .cm-content").first()).toContainText(
      "Reproducible E2E manuscript",
    );
    await expect(first.page.locator(".editor-pane .cm-content").first()).not.toContainText(
      "Temporary revision",
    );
    const discardSync = await first.page.evaluate(async ({ origin, id }) => {
      const response = await fetch(`${origin}/api/writing/${id}/sync-status`);
      return (await response.json()) as { state: string };
    }, { origin: info.backendOrigin, id: projectId });
    expect(discardSync.state).toBe("in_sync");

    const recoveryFiles = (await filesBelow(conflictDirectory)).filter((candidate) => {
      const topLevel = path.relative(conflictDirectory, candidate).split(path.sep)[0];
      return !conflictsBeforeDiscard.has(topLevel);
    });
    const patchFiles = recoveryFiles.filter(
      (candidate) => path.basename(candidate) === "tracked-changes.patch",
    );
    expect(patchFiles).toHaveLength(1);
    expect((await fs.stat(patchFiles[0])).size).toBeGreaterThan(0);
    expect(await fs.readFile(patchFiles[0], "utf8")).toContain("Temporary revision");
    const recoveredManuscripts = await Promise.all(
      recoveryFiles
        .filter((candidate) => candidate.endsWith(".md"))
        .map((candidate) => fs.readFile(candidate, "utf8").catch(() => "")),
    );
    expect(recoveredManuscripts.some((content) => content.includes("Temporary revision"))).toBe(
      true,
    );

    await first.page.getByRole("button", { name: /^(版本|Versions)$/ }).click();
    await expect(
      first.page.getByText("before discarding Git changes", { exact: true }),
    ).toBeVisible();

    // The untracked note remains after discard. Creating an exploration branch
    // carries it safely; the next explicit version save commits it on that branch.
    const branchesCard = first.page.getByLabel(/^(Git 分支|Git branches)$/);
    await branchesCard
      .getByLabel(/^(新分支名称|New branch name)$/)
      .fill(branchName);
    await branchesCard
      .getByRole("button", { name: /^(创建并切换|Create and switch)$/ })
      .click();
    await expect(
      first.page.getByText(/^(已创建并切换分支|Branch created and checked out)$/),
    ).toBeVisible();
    await expect(branchesCard).toContainText(`${branchName} ·`);
    await expect(branchesCard).toContainText(new RegExp(`(?:当前|current): ${branchName}`));

    await first.page.getByRole("button", { name: /^(手稿|Manuscript)$/ }).click();
    const branchEditor = first.page.locator(".editor-pane .cm-content").first();
    await branchEditor.fill(branchText);
    await first.page.getByRole("button", { name: /^(保存|Save)/ }).click();
    await expect(first.page.getByRole("button", { name: /^(保存|Save)/ })).toBeDisabled();
    await first.page.getByRole("button", { name: /^(版本|Versions)$/ }).click();
    await first.page
      .getByPlaceholder(/^(版本说明|What changed)/)
      .fill(branchVersionLabel);
    await first.page.getByRole("button", { name: /^(本地提交版本|Commit local version)$/ }).click();
    const branchVersionRows = first.page.locator("tbody tr").filter({ hasText: branchVersionLabel });
    await expect(branchVersionRows).toHaveCount(2);
    await expect(branchVersionRows.getByText("git", { exact: true })).toBeVisible();
    await expect(branchVersionRows.getByText("snapshot", { exact: true })).toBeVisible();

    const targetBranch = branchesCard.getByLabel(/^(目标分支|Target branch)$/);
    await targetBranch.selectOption("main");
    first.page.once("dialog", (dialog) => {
      expect(dialog.message()).toContain("main");
      return dialog.accept();
    });
    await branchesCard.getByRole("button", { name: /^(切换分支|Switch branch)$/ }).click();
    await expect(first.page.getByText(/^(分支已切换|Branch switched)$/)).toBeVisible();
    await expect(branchesCard).toContainText(/(?:当前|current): main/);
    await first.page.getByRole("button", { name: /^(手稿|Manuscript)$/ }).click();
    await expect(first.page.locator(".editor-pane .cm-content").first()).toContainText(
      "Reproducible E2E manuscript",
    );
    await expect(first.page.locator(".editor-pane .cm-content").first()).not.toContainText(
      "Branch-only rewrite",
    );
    await expect.poll(
      () => fs.stat(untrackedPath).then(() => true).catch(() => false),
      { message: "main must not contain the note committed only on the exploration branch" },
    ).toBe(false);

    await first.page.getByRole("button", { name: /^(版本|Versions)$/ }).click();
    await branchesCard.getByLabel(/^(目标分支|Target branch)$/).selectOption(branchName);
    first.page.once("dialog", (dialog) => dialog.accept());
    await branchesCard.getByRole("button", { name: /^(切换分支|Switch branch)$/ }).click();
    await expect(branchesCard).toContainText(new RegExp(`(?:当前|current): ${branchName}`));
    await first.page.getByRole("button", { name: /^(手稿|Manuscript)$/ }).click();
    await expect(first.page.locator(".editor-pane .cm-content").first()).toContainText(
      "Branch-only rewrite",
    );
    expect(await fs.readFile(untrackedPath, "utf8")).toContain("must survive");

    await first.page.getByRole("button", { name: /^(版本|Versions)$/ }).click();
    await branchesCard.getByLabel(/^(目标分支|Target branch)$/).selectOption("main");
    first.page.once("dialog", (dialog) => dialog.accept());
    await branchesCard.getByRole("button", { name: /^(切换分支|Switch branch)$/ }).click();
    await expect(branchesCard).toContainText(/(?:当前|current): main/);

    // Keep the original snapshot comparison and restore contract covered after
    // Git discard/branch switching have returned the project to the baseline.
    await first.page.getByRole("button", { name: /^(手稿|Manuscript)$/ }).click();
    await first.page.locator(".editor-pane .cm-content").first().fill(revisedText);
    await first.page.getByRole("button", { name: /^(保存|Save)/ }).click();
    await expect(first.page.getByRole("button", { name: /^(保存|Save)/ })).toBeDisabled();
    await first.page.getByRole("button", { name: /^(版本|Versions)$/ }).click();
    await baselineRow
      .getByRole("button", { name: /^(与当前对比|Diff vs current)$/ })
      .click();
    await expect(diffModal.getByText("Temporary revision", { exact: false })).toBeVisible();
    await diffModal.locator("header button").click();
    await expect(diffModal).toBeHidden();

    first.page.once("dialog", (dialog) => dialog.accept());
    await baselineRow.getByRole("button", { name: /^(回滚|Restore)$/ }).click();
    await expect(first.page.getByText(/^(已回滚|Restored)$/)).toBeVisible();
    await first.page.getByRole("button", { name: /^(手稿|Manuscript)$/ }).click();
    await expect(first.page.locator(".editor-pane .cm-content").first()).toContainText(
      "Reproducible E2E manuscript",
    );
    await expect(first.page.locator(".editor-pane .cm-content").first()).not.toContainText(
      "Temporary revision",
    );

    await first.page.getByRole("button", { name: /^(导出|Export)$/ }).click();
    await expect(first.page.getByRole("heading", { name: /^(导出|Export)$/ })).toBeVisible();
    const markdownCard = first.page.locator(".card").filter({
      has: first.page.getByRole("heading", { name: "Markdown", exact: true }),
    });
    await markdownCard.getByRole("button", { name: /^(导出|Export)$/ }).click();
    await expect(first.page.getByText(/^markdown (?:已写入|written to) /)).toBeVisible();
    const docxCard = first.page.locator(".card").filter({
      has: first.page.getByRole("heading", { name: "Word (.docx)", exact: true }),
    });
    await docxCard.getByRole("button", { name: /^(导出|Export)$/ }).click();
    await expect(first.page.getByText(/^docx (?:已写入|written to) /)).toBeVisible();
    const latexCard = first.page.locator(".card").filter({
      has: first.page.getByRole("heading", { name: "LaTeX", exact: true }),
    });
    await latexCard.getByRole("button", { name: /^(导出|Export)$/ }).click();
    await expect(first.page.getByText(/^latex (?:已写入|written to) /)).toBeVisible();
    const bibtexCard = first.page.locator(".card").filter({
      has: first.page.getByRole("heading", { name: "BibTeX", exact: true }),
    });
    await bibtexCard.getByRole("button", { name: /^(导出|Export)$/ }).click();
    await expect(first.page.getByText(/^bibtex (?:已写入|written to) /)).toBeVisible();
    const bundleCard = first.page.locator(".card").filter({
      has: first.page.getByRole("heading", { name: /^(打包|Bundle) \(\.zip\)$/ }),
    });
    await bundleCard.getByRole("button", { name: /^(导出|Export)$/ }).click();
    await expect(first.page.getByText(/^zip (?:已写入|written to) /)).toBeVisible();
    await first.page
      .getByRole("button", { name: /^(生成 Overleaf 上传包|Build Overleaf upload archive)$/ })
      .click();
    await expect(first.page.getByText(/^(Overleaf 上传包已就绪|Overleaf archive ready)$/)).toBeVisible();

    const exportDirectory = path.join(managed, "projects", projectDirectory, "exports");
    const markdownExport = path.join(exportDirectory, `${projectDirectory}.md`);
    const docxExport = path.join(exportDirectory, `${projectDirectory}.docx`);
    const latexDirectory = path.join(exportDirectory, `${projectDirectory}-latex`);
    const bibtexExport = path.join(exportDirectory, `${projectDirectory}.bib`);
    const bundleExport = path.join(exportDirectory, `${projectDirectory}-bundle.zip`);
    const overleafExport = path.join(exportDirectory, `${projectDirectory}-overleaf.zip`);
    await expect.poll(async () => {
      const markdown = (await fs.readFile(markdownExport, "utf8")).replace(/\r\n/g, "\n");
      const docx = await fs.readFile(docxExport);
      const mainTex = await fs.readFile(path.join(latexDirectory, "main.tex"), "utf8");
      const references = await fs.readFile(path.join(latexDirectory, "references.bib"), "utf8");
      const sectionFiles = (await filesBelow(path.join(latexDirectory, "sections"))).filter(
        (candidate) => candidate.endsWith(".tex"),
      );
      const sections = (
        await Promise.all(sectionFiles.map((candidate) => fs.readFile(candidate, "utf8")))
      ).join("\n");
      const bibliography = await fs.readFile(bibtexExport, "utf8");
      const bundle = await fs.readFile(bundleExport);
      const overleaf = await fs.readFile(overleafExport);
      const bundleEntries = zipEntryNames(bundle);
      const overleafEntries = zipEntryNames(overleaf);
      return {
        restoredText: markdown.includes("Reproducible E2E manuscript"),
        temporaryText: markdown.includes("Temporary revision"),
        docxIsZip: docx[0] === 0x50 && docx[1] === 0x4b,
        docxBytes: docx.length,
        latexClass: /\\documentclass(?:\[[^\]]+\])?\{article\}/.test(mainTex),
        latexIncludesSections: mainTex.includes("\\input{sections/"),
        latexRestoredText: `${mainTex}\n${sections}`.includes("Reproducible E2E manuscript"),
        latexTemporaryText: `${mainTex}\n${sections}`.includes("Temporary revision"),
        latexCitation: `${mainTex}\n${sections}`.includes("\\cite{researcher2012}"),
        latexBibliography: references.includes("@article{RESEARCHER2012"),
        standaloneBibtex: bibliography.includes("@article{RESEARCHER2012"),
        bundleEntries,
        overleafEntries,
      };
    }, { message: "all built-in exports should contain the restored manuscript" }).toEqual({
      restoredText: true,
      temporaryText: false,
      docxIsZip: true,
      docxBytes: expect.any(Number),
      latexClass: true,
      latexIncludesSections: true,
      latexRestoredText: true,
      latexTemporaryText: false,
      latexCitation: true,
      latexBibliography: true,
      standaloneBibtex: true,
      bundleEntries: expect.arrayContaining([
        `${projectDirectory}.md`,
        `${projectDirectory}.docx`,
        `${projectDirectory}.bib`,
        "latex/main.tex",
        "latex/references.bib",
      ]),
      overleafEntries: expect.arrayContaining([
        "main.tex",
        "references.bib",
      ]),
    });
    expect((await fs.stat(docxExport)).size).toBeGreaterThan(1_000);
    expect((await fs.stat(bundleExport)).size).toBeGreaterThan(1_000);
    expect((await fs.stat(overleafExport)).size).toBeGreaterThan(500);

    const restartResult = await first.page.evaluate(() =>
      (window as unknown as {
        papercreator: { backend: { restart(): Promise<{ origin?: string; error?: string }> } };
      }).papercreator.backend.restart(),
    );
    expect(restartResult.error).toBeFalsy();
    await expect.poll(async () => {
      const infoAfterRestart = await first.page.evaluate(() =>
        (window as unknown as {
          papercreator: { appInfo(): Promise<{ backendRunning: boolean }> };
        }).papercreator.appInfo(),
      );
      return infoAfterRestart.backendRunning;
    }, { timeout: 90_000, message: "the main process should restart the backend" }).toBe(true);

    await first.page.reload();
    await expect(first.page.locator(".titlebar .project-pill .name")).toHaveText(
      projectTitle,
      { timeout: 90_000 },
    );
    // The title-bar project identity and close control together prove that boot
    // reopened the remembered project and project-only navigation is ready.
    await expect(first.page.getByRole("button", { name: /^(关闭项目|Close project)$/ })).toBeVisible({
      timeout: 90_000,
    });
    await first.page.getByRole("button", { name: /^(手稿|Manuscript)$/ }).click();
    await expect(first.page.locator(".editor-pane .cm-content").first()).toContainText(
      "Reproducible E2E manuscript",
    );

    // Chromium reports the intentionally severed SSE/HTTP connection while the
    // backend process is replaced. Reject every other renderer error.
    expect(
      consoleErrors.filter(
        (message) => ![
          "Failed to load resource: net::ERR_CONNECTION_RESET",
          "Failed to load resource: net::ERR_CONNECTION_REFUSED",
          "Failed to load resource: net::ERR_INCOMPLETE_CHUNKED_ENCODING",
        ].includes(message),
      ),
    ).toEqual([]);
    consoleErrors.length = 0;
    first.page.off("pageerror", onPageError);
    first.page.off("console", onConsole);
    await closeApplication(running);
    running = null;

    const second = await launch(workbench, mockLlm.baseUrl, mockOpenAlex.endpoint, false);
    running = second.application;
    await expect(second.page.getByRole("dialog", { name: /^(快速开始|Quick start)$/ })).toHaveCount(0);
    const secondInfo = await second.page.evaluate(() =>
      (window as unknown as {
        papercreator: { appInfo(): Promise<{ backendOrigin: string }> };
      }).papercreator.appInfo(),
    );
    second.page.on("pageerror", onPageError);
    second.page.on("console", onConsole);
    await expect(second.page.locator(".titlebar .project-pill .name")).toHaveText(
      projectTitle,
      { timeout: 90_000 },
    );
    await expect(second.page.getByRole("button", { name: /^(关闭项目|Close project)$/ })).toBeVisible({
      timeout: 90_000,
    });
    const restoredAssistant = second.page.locator(".assistant-panel");
    if (!(await restoredAssistant.isVisible())) {
      await second.page.getByRole("button", { name: /^(AI 助手|AI assistant)$/ }).click();
    }
    await expect(restoredAssistant.getByText(/^\{"ok":\s*true\}$/)).toBeVisible();
    await expect(restoredAssistant.getByRole("combobox", { name: /^(对话历史|Conversation history)$/ }))
      .toHaveValue(durableAssistantThread);
    await restoredAssistant.getByTitle(/^(关闭|Close)$/).click();
    // Reopening the remembered project intentionally shows its manuscript, not
    // the workbench-home resource cards.  Verify the idea's durable record via
    // the real backend; its creation and card rendering were already exercised
    // before the restart.
    const restoredIdeas = await second.page.evaluate(async (origin) => {
      const response = await fetch(`${origin}/api/workbench/resources?kind=idea`);
      return response.json() as Promise<{ items: Array<{ title: string }> }>;
    }, secondInfo.backendOrigin);
    expect(restoredIdeas.items.some((item) => item.title === ideaTitle)).toBe(true);
    await second.page.getByRole("button", { name: /^(检索|Search)$/ }).click();
    const restoredSearchHistory = second.page.getByRole("table", {
      name: /^(检索历史|Search history)$/,
    });
    await expect(restoredSearchHistory.locator("tbody tr")).toHaveCount(3);
    await expect(restoredSearchHistory).toContainText("idea");
    await expect(restoredSearchHistory).toContainText("paper");
    await second.page.getByRole("button", { name: /^(文献库|Library)$/ }).click();
    await expect(second.page.getByText(/13 显示\s*\/\s*13 匹配/)).toBeVisible();
    await expect(
      second.page.locator("tbody tr").filter({ hasText: ideaTitleForMap }),
    ).toBeVisible();
    await second.page.getByRole("button", { name: /^(研究图谱|Landscape)$/ }).click();
    await expect(second.page.locator(".landscape")).toBeVisible();
    await expect(second.page.locator(".overlay.bl")).toContainText("13 篇");
    await expect(second.page.getByText("我的想法 / 论文", { exact: true })).toBeVisible();
    await second.page.getByRole("button", { name: /^(智能体|Agents)$/ }).click();
    await expect(
      second.page.getByRole("heading", { name: /^(尚未配置模型|No model configured)$/ }),
    ).toBeVisible();
    const restoredAgentRun = second.page
      .getByRole("table", { name: /^(运行记录|Run history)$/ })
      .locator("tbody tr")
      .filter({ hasText: /(撰写指定章节|section)/ });
    await expect(restoredAgentRun).toHaveCount(2);
    const restoredDoneRun = restoredAgentRun.filter({ hasText: /(已完成|done)/ });
    const restoredFailedRun = restoredAgentRun.filter({ hasText: /(失败|failed)/ });
    await expect(restoredDoneRun).toBeVisible();
    await expect(restoredFailedRun).toBeVisible();
    await expect(restoredDoneRun).toContainText("4");
    await expect(restoredFailedRun).toContainText("4");
    await restoredDoneRun.getByRole("button", { name: /^(查看|Open)$/ }).click();
    const restoredQuality = second.page.getByRole("region", {
      name: /^(论文质量报告|Manuscript quality report)$/,
    });
    await expect(restoredQuality).toContainText("E2E Research Lead");
    await expect(restoredQuality).toContainText("accepted");
    await expect(
      second.page.getByRole("region", {
        name: /^(人工质量评审摘要|Human quality review summary)$/,
      }),
    ).toContainText(/1 个已评运行|1 reviewed runs/);
    const restartedFailure = await second.page.evaluate(async ({ origin, runId }) => {
      const response = await fetch(`${origin}/api/agents/runs/${runId}`);
      return response.json();
    }, { origin: secondInfo.backendOrigin, runId: durableFailure!.id }) as {
      status: string;
      result: { failure: { outcome: string }; recovery: { strategy: string } };
      steps: Array<{ agent: string; status: string; meta: Record<string, any> }>;
    };
    expect(restartedFailure.status).toBe("failed");
    expect(restartedFailure.result.failure.outcome).toBe("stream_interrupted");
    expect(restartedFailure.result.recovery.strategy).toBe("partial_work_preserved");
    expect(restartedFailure.steps.find((step) => step.agent === "writer")?.meta.failure.outcome)
      .toBe("stream_interrupted");
    await second.page.getByRole("button", { name: /^(版本|Versions)$/ }).click();
    await expect(second.page.locator("tbody tr").filter({ hasText: versionLabel })).toHaveCount(2);
    await second.page.getByRole("button", { name: /^(导出|Export)$/ }).click();
    await expect(second.page.getByText(`${projectDirectory}.md`, { exact: true })).toBeVisible();
    await expect(second.page.getByText(`${projectDirectory}.docx`, { exact: true })).toBeVisible();
    await expect(second.page.getByText(`${projectDirectory}.bib`, { exact: true })).toBeVisible();
    await expect(second.page.getByText(`${projectDirectory}-bundle.zip`, { exact: true })).toBeVisible();
    await expect(second.page.getByText(`${projectDirectory}-overleaf.zip`, { exact: true })).toBeVisible();
    await expect(second.page.getByText(`${projectDirectory}-latex\\main.tex`, { exact: true })).toBeVisible();
    await second.page.getByRole("button", { name: /^(手稿|Manuscript)$/ }).click();
    await expect(second.page.locator(".editor-pane .cm-content").first()).toContainText(
      "Reproducible E2E manuscript",
    );
    expect(consoleErrors).toEqual([]);

    // Closing a project returns to the workbench; the enlarged project card is
    // the entry point back into that paper rather than a passive summary.
    await second.page.getByRole("button", { name: /^(关闭项目|Close project)$/ }).click();
    const reopenedCard = second.page.locator(".project-card").filter({ hasText: projectTitle });
    await expect(reopenedCard).toBeVisible();

    // Import the project archive into the workbench scope through the narrow
    // native archive reader. The preview must show one new copied thread, and
    // restored suggestions remain suggestions rather than executed actions.
    const workbenchAssistantRail = second.page.getByTitle(/^(打开 AI 助手|Open AI assistant)$/);
    if (await workbenchAssistantRail.isVisible()) await workbenchAssistantRail.click();
    const workbenchAssistant = second.page.locator(".assistant-panel");
    await workbenchAssistant.getByTitle(/^(管理对话数据|Manage conversation data)$/).click();
    modal = second.page.locator(".modal");
    await modal.getByRole("button", { name: /^(导入对话归档|Import conversation archive)$/ }).click();
    await expect(modal.getByText(/^(导入预览|Import preview)$/)).toBeVisible();
    await expect(modal.locator(".assistant-import-preview")).toContainText(/将新增 1 个|1 new/);
    const importConfirm = modal.getByRole("checkbox", {
      name: /^(我确认把新对话导入当前范围|I confirm importing new conversations into the current scope)$/,
    });
    await importConfirm.check();
    await modal.getByRole("button", { name: /^(确认导入|Confirm import)$/ }).click();
    await expect(modal.locator(".assistant-governance-stats dd").nth(0)).toHaveText("1");
    await modal.getByRole("button", { name: /^(关闭对话数据管理|Close conversation data)$/ }).click();
    await expect(workbenchAssistant.getByText(/^\{"ok":\s*true\}$/)).toBeVisible();
    const redactButtons = workbenchAssistant.getByRole("button", {
      name: /^(清除敏感内容|Remove sensitive content)$/,
    });
    await expect(redactButtons).toHaveCount(2);
    await redactButtons.nth(1).click();
    modal = second.page.locator(".modal");
    await expect(modal.getByText(/^(清除消息敏感内容|Remove sensitive message content)$/)).toBeVisible();
    await modal.getByLabel(/^(清理原因.*|Reason.*)$/).fill("E2E privacy verification");
    await modal.getByRole("checkbox", {
      name: /^(我确认永久清除此消息的敏感内容|I confirm permanently removing this message's sensitive content)$/,
    }).check();
    await modal.getByRole("button", { name: /^(永久清除|Remove permanently)$/ }).click();
    await expect(workbenchAssistant.getByText("[Content removed by user]", { exact: true })).toBeVisible();
    await expect(workbenchAssistant.getByRole("button", { name: /^(打开文献检索|Open literature search)$/ })).toHaveCount(0);
    await workbenchAssistant.getByTitle(/^(关闭|Close)$/).click();

    await reopenedCard.click();
    await expect(second.page.locator(".titlebar .project-pill .name")).toHaveText(projectTitle);
    await expect(second.page.locator(".editor-pane .cm-content").first()).toContainText(
      "Reproducible E2E manuscript",
    );

    // Interface language is one language at a time and is persisted in the
    // selected workbench, including across a complete Electron/backend restart.
    await second.page.getByRole("button", { name: /^(设置|Settings)$/ }).click();
    await second.page.getByLabel(/^(应用显示语言|Application display language)$/).selectOption("en-US");
    await expect(second.page.getByRole("button", { name: "File", exact: true })).toBeVisible();
    await expect(second.page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();
    await closeApplication(running);
    running = null;

    const third = await launch(workbench, mockLlm.baseUrl, mockOpenAlex.endpoint, false);
    running = third.application;
    third.page.on("pageerror", onPageError);
    third.page.on("console", onConsole);
    await expect(third.page.getByRole("button", { name: "File", exact: true })).toBeVisible();
    await expect(third.page.locator(".titlebar .project-pill .name")).toHaveText(projectTitle);
    await third.page.getByRole("button", { name: "Manuscript", exact: true }).click();
    await expect(third.page.locator(".editor-pane .cm-content").first()).toContainText(
      "Reproducible E2E manuscript",
    );
    expect(consoleErrors).toEqual([]);

    // A native quit must save Renderer-owned dirty text before it closes the
    // SSE connection/backend. Do not click Save or press Ctrl+S here.
    const closeSavedMarker = "Saved automatically during Electron quit.";
    const thirdEditor = third.page.locator(".editor-pane .cm-content").first();
    const beforeCloseText = await thirdEditor.innerText();
    await thirdEditor.fill(`${beforeCloseText}\n\n${closeSavedMarker}`);
    await expect(third.page.getByText(/● 1 unsaved/)).toBeVisible();
    third.page.off("pageerror", onPageError);
    third.page.off("console", onConsole);
    await closeApplication(running);
    running = null;

    const fourth = await launch(workbench, mockLlm.baseUrl, mockOpenAlex.endpoint, false);
    running = fourth.application;
    fourth.page.on("pageerror", onPageError);
    fourth.page.on("console", onConsole);
    await expect(fourth.page.locator(".titlebar .project-pill .name")).toHaveText(projectTitle);
    await fourth.page.getByRole("button", { name: "Manuscript", exact: true }).click();
    await expect(fourth.page.locator(".editor-pane .cm-content").first()).toContainText(
      closeSavedMarker,
    );
    expect(consoleErrors).toEqual([]);

    // Close through the user-facing Electron lifecycle, then prove that the
    // actual Python service completed FastAPI shutdown and SQLite checkpoint.
    await closeApplication(running);
    running = null;
    const backendLifecycleLog = await fs.readFile(
      path.join(managed, "logs", "papercreator.log"),
      "utf8",
    );
    const desktopLifecycleLog = await fs.readFile(
      path.join(managed, "logs", "desktop.log"),
      "utf8",
    );
    expect(backendLifecycleLog).toContain("SQLite WAL checkpoint complete");
    expect(backendLifecycleLog).toContain("Application shutdown complete");
    expect(desktopLifecycleLog).toMatch(/backend exited \(code 0, signal null\)/);
  } finally {
    if (running) await closeApplication(running).catch(() => undefined);
    await mockOpenAlex.close().catch(() => undefined);
    await mockLlm.close().catch(() => undefined);
    await removeTemporaryWorkbench(workbench);
  }
});
