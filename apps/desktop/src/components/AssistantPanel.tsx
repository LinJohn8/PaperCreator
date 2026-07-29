import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import * as endpoints from "../api/endpoints";
import type {
  AssistantAction,
  AssistantConversationExport,
  AssistantImportPreview,
  AssistantMaintenancePreview,
  AssistantMessageRedactionPreview,
  AssistantScopeStats,
  AssistantThread,
  PromptTemplate,
  SkillDraft,
} from "../api/types";
import { useStore } from "../state/store";

type ChatLine = {
  id: string;
  role: "user" | "assistant";
  content: string;
  actions?: AssistantAction[];
  meta?: string;
  redacted?: boolean;
};

let lineCounter = 0;

export function AssistantPanel() {
  const open = useStore((s) => s.assistantOpen);
  const toggle = useStore((s) => s.toggleAssistant);
  const locale = useStore((s) => s.locale);
  const project = useStore((s) => s.project);
  const document = useStore((s) => s.document);
  const activeSectionKey = useStore((s) => s.activeSectionKey);
  const enabledSkillIds = useStore((s) => s.enabledSkillIds);
  const hasLlm = useStore((s) => s.health?.llm.has_any ?? false);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const setView = useStore((s) => s.setView);
  const editSection = useStore((s) => s.editSection);
  const saveAllSections = useStore((s) => s.saveAllSections);
  const loadSkills = useStore((s) => s.loadSkills);

  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [messages, setMessages] = useState<ChatLine[]>([]);
  const [threads, setThreads] = useState<AssistantThread[]>([]);
  const [threadId, setThreadId] = useState("");
  const [governanceOpen, setGovernanceOpen] = useState(false);
  const [promptLibrary, setPromptLibrary] = useState(false);
  const [templateFill, setTemplateFill] = useState<PromptTemplate | null>(null);
  const [skillDraft, setSkillDraft] = useState<SkillDraft | null>(null);
  const [skillBusy, setSkillBusy] = useState(false);
  const [commitDraft, setCommitDraft] = useState<string | null>(null);
  const [commitBusy, setCommitBusy] = useState(false);
  const [redactionPreview, setRedactionPreview] = useState<AssistantMessageRedactionPreview | null>(null);
  const [redactionBusy, setRedactionBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const threadScopeVersion = useRef(0);

  const activeSection = useMemo(
    () => document?.sections.find((section) => section.key === activeSectionKey) ?? null,
    [activeSectionKey, document],
  );

  const loadThread = useCallback(async (id: string, expectedVersion?: number) => {
    if (!id) {
      if (expectedVersion !== undefined && expectedVersion !== threadScopeVersion.current) return;
      setMessages([]);
      setThreadId("");
      return;
    }
    const restored = await endpoints.assistant.thread(id);
    if (expectedVersion !== undefined && expectedVersion !== threadScopeVersion.current) return;
    setThreadId(id);
    setMessages(restored.messages.map((message) => {
      const meta = message.meta as {
        provider?: string;
        model?: string;
        usage?: { prompt_tokens?: number; completion_tokens?: number };
        redaction?: Record<string, unknown>;
      };
      const tokens = Number(meta.usage?.prompt_tokens ?? 0) + Number(meta.usage?.completion_tokens ?? 0);
      return {
        id: message.id,
        role: message.role,
        content: message.content,
        actions: message.actions,
        redacted: Boolean(meta.redaction),
        meta: message.role === "assistant" && meta.provider
          ? `${meta.provider}:${meta.model ?? ""}${tokens ? ` · ${tokens} tok` : ""}`
          : undefined,
      };
    }));
  }, []);

  const refreshThreads = useCallback(async (preferred = "") => {
    const version = ++threadScopeVersion.current;
    const scope = project?.id ?? "";
    const result = await endpoints.assistant.threads(scope);
    if (version !== threadScopeVersion.current) return;
    setThreads(result.items);
    const next = preferred || result.items[0]?.id || "";
    await loadThread(next, version);
  }, [loadThread, project?.id]);

  useEffect(() => {
    if (!open) {
      threadScopeVersion.current += 1;
      return;
    }
    void refreshThreads().catch((error) =>
      reportError(error, locale === "zh-CN" ? "恢复 AI 对话" : "restoring assistant chat"),
    );
  }, [open, project?.id, refreshThreads]);

  async function send() {
    const message = input.trim();
    if (!message || busy) return;
    const userLine: ChatLine = { id: `chat-${++lineCounter}`, role: "user", content: message };
    const next = [...messages, userLine];
    setMessages(next);
    setInput("");
    setBusy(true);
    queueMicrotask(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }));
    let createdThreadId = "";
    try {
      let activeThreadId = threadId;
      if (!activeThreadId) {
        const created = await endpoints.assistant.createThread(project?.id ?? "");
        activeThreadId = created.thread.id;
        createdThreadId = activeThreadId;
        setThreadId(activeThreadId);
      }
      const result = await endpoints.assistant.chat({
        message,
        project_id: project?.id ?? "",
        section_key: activeSectionKey,
        history: messages.slice(-20).map(({ role, content }) => ({ role, content })),
        skill_ids: enabledSkillIds,
        locale,
        thread_id: activeThreadId,
      });
      setMessages([
        ...next,
        {
          id: `chat-${++lineCounter}`,
          role: "assistant",
          content: result.answer,
          actions: result.suggested_actions,
          meta: `${result.provider}:${result.model} · ${result.usage.prompt_tokens + result.usage.completion_tokens} tok`,
        },
      ]);
      const latestThreads = await endpoints.assistant.threads(project?.id ?? "");
      setThreads(latestThreads.items);
      if (result.skill_problems.length) {
        notify({
          kind: "warning",
          message: locale === "zh-CN" ? "部分技能未注入" : "Some skills were not injected",
          detail: result.skill_problems.join("; "),
        });
      }
    } catch (error) {
      if (createdThreadId) {
        await endpoints.assistant.deleteThread(createdThreadId).catch(() => undefined);
        setThreadId("");
      }
      reportError(error, locale === "zh-CN" ? "AI 对话" : "assistant chat");
    } finally {
      setBusy(false);
      queueMicrotask(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }));
    }
  }

  async function runAction(action: AssistantAction, answer: string) {
    if (action.kind === "open_search") {
      setView("search");
      return;
    }
    if (action.kind === "insert_into_section") {
      if (!project || !activeSection) return;
      const ok = window.confirm(
        locale === "zh-CN"
          ? `把这条回复追加到“${activeSection.title_zh || activeSection.title}”的本地草稿吗？追加后仍需 Ctrl+S 保存。`
          : `Append this response to the local draft of “${activeSection.title}”? It will remain unsaved until Ctrl+S.`,
      );
      if (!ok) return;
      const state = useStore.getState();
      const current = state.dirtySections[activeSection.key] ?? activeSection.content;
      editSection(activeSection.key, `${current.trimEnd()}\n\n${answer.trim()}\n`);
      notify({
        kind: "success",
        message: locale === "zh-CN" ? "已追加到手稿草稿（尚未保存）" : "Appended to the manuscript draft (unsaved)",
      });
      return;
    }
    if (action.kind === "commit_local_version") {
      if (!project) return;
      setCommitDraft(String(action.payload.message || (locale === "zh-CN" ? "论文工作进度" : "Paper progress")));
      return;
    }
    if (action.kind === "draft_skill") {
      setSkillBusy(true);
      try {
        const result = await endpoints.skills.draft(String(action.payload.request || ""));
        setSkillDraft(result.draft);
      } catch (error) {
        reportError(error, locale === "zh-CN" ? "生成技能草稿" : "drafting a skill");
      } finally {
        setSkillBusy(false);
      }
    }
  }

  async function commitLocalVersion(message: string) {
    if (!project || !message.trim()) return;
    setCommitBusy(true);
    try {
      await saveAllSections();
      await endpoints.versions.commit(project.id, message.trim(), true);
      notify({
        kind: "success",
        message: locale === "zh-CN" ? "本地版本已提交（未推送）" : "Local version committed (not pushed)",
      });
      setCommitDraft(null);
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "提交本地版本" : "committing a local version");
    } finally {
      setCommitBusy(false);
    }
  }

  async function previewMessageRedaction(messageId: string) {
    setRedactionBusy(true);
    try {
      setRedactionPreview(await endpoints.assistant.previewMessageRedaction(messageId));
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "预览消息敏感内容清理" : "previewing message redaction");
    } finally {
      setRedactionBusy(false);
    }
  }

  async function executeMessageRedaction(reason: string) {
    if (!redactionPreview) return;
    setRedactionBusy(true);
    try {
      await endpoints.assistant.executeMessageRedaction(
        redactionPreview.message_id,
        redactionPreview,
        reason,
      );
      const preferred = redactionPreview.thread_id;
      setRedactionPreview(null);
      await refreshThreads(preferred);
      notify({
        kind: "success",
        message: locale === "zh-CN" ? "敏感消息内容已不可逆清除" : "Sensitive message content was irreversibly removed",
      });
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "清除消息敏感内容" : "redacting message content");
      setRedactionPreview(null);
      await refreshThreads(threadId).catch(() => undefined);
    } finally {
      setRedactionBusy(false);
    }
  }

  if (!open) {
    return (
      <button
        className="assistant-rail"
        onClick={() => toggle(true)}
        title={locale === "zh-CN" ? "打开 AI 助手" : "Open AI assistant"}
      >
        ◇
      </button>
    );
  }

  return (
    <aside className="assistant-panel">
      <header className="assistant-header">
        <div className="grow">
          <strong>{locale === "zh-CN" ? "AI 研究助手" : "AI research assistant"}</strong>
          <div className="dim truncate">
            {project
              ? `${project.title}${activeSection ? ` · ${activeSection.title_zh || activeSection.title}` : ""}`
              : locale === "zh-CN" ? "未打开论文项目" : "No paper project open"}
          </div>
        </div>
        <button className="btn icon sm" onClick={() => void loadThread("")} title={locale === "zh-CN" ? "新对话" : "New chat"}>＋</button>
        <button
          className="btn icon sm"
          onClick={() => setGovernanceOpen(true)}
          title={locale === "zh-CN" ? "管理对话数据" : "Manage conversation data"}
        >⚙</button>
        <button
          className="btn icon sm"
          disabled={!threadId}
          onClick={async () => {
            if (!threadId || !window.confirm(locale === "zh-CN" ? "删除当前对话？" : "Delete this conversation?")) return;
            try {
              await endpoints.assistant.deleteThread(threadId);
              await refreshThreads();
            } catch (error) {
              reportError(error, locale === "zh-CN" ? "删除 AI 对话" : "deleting assistant chat");
            }
          }}
          title={locale === "zh-CN" ? "删除当前对话" : "Delete current chat"}
        >×</button>
        <button className="btn icon sm" onClick={() => toggle(false)} title={locale === "zh-CN" ? "关闭" : "Close"}>×</button>
      </header>

      <div className="assistant-toolbar">
        <select
          aria-label={locale === "zh-CN" ? "对话历史" : "Conversation history"}
          value={threadId}
          onChange={(event) => void loadThread(event.target.value)}
          style={{ minWidth: 0, maxWidth: 180 }}
        >
          <option value="">{locale === "zh-CN" ? "新对话" : "New conversation"}</option>
          {threads.map((thread) => (
            <option key={thread.id} value={thread.id}>{thread.title || (locale === "zh-CN" ? "未命名对话" : "Untitled")}</option>
          ))}
        </select>
        <button className="btn sm" onClick={() => setPromptLibrary(true)}>
          {locale === "zh-CN" ? "提示词模板" : "Prompt templates"}
        </button>
        <button className="btn sm" onClick={() => setView("skills")}>
          {locale === "zh-CN" ? `技能 ${enabledSkillIds.length}` : `Skills ${enabledSkillIds.length}`}
        </button>
        <span className={`chip ${hasLlm ? "ok" : "warn"}`}>
          {hasLlm ? (locale === "zh-CN" ? "模型可用" : "model ready") : (locale === "zh-CN" ? "未配置模型" : "no model")}
        </span>
      </div>

      <div className="assistant-messages" ref={scrollRef}>
        {messages.length === 0 && (
          <div className="assistant-welcome">
            <div className="big">◇</div>
            <p>
              {locale === "zh-CN"
                ? "我会读取当前项目、章节结构和项目文献来回答。可让我修改文字、规划检索、起草 Skill，或创建经你确认的本地 Git 版本。"
                : "I use the current project, outline and project papers as context. Ask for revisions, search planning, a Skill draft, or a confirmed local Git checkpoint."}
            </p>
            <p className="dim">
              {locale === "zh-CN"
                ? "所有写入操作都需要确认；不会自动推送或执行危险命令。"
                : "Every write requires confirmation; no automatic pushes or dangerous commands."}
            </p>
          </div>
        )}
        {messages.map((line) => (
          <div key={line.id} className={`assistant-message ${line.role}`}>
            <div className="assistant-role">{line.role === "user" ? (locale === "zh-CN" ? "你" : "You") : "PaperCreator AI"}</div>
            <div className="assistant-text">{line.content}</div>
            {line.meta && <div className="dim assistant-meta">{line.meta}</div>}
            {!line.redacted && !line.id.startsWith("chat-") && (
              <button
                className="assistant-redact-message"
                disabled={redactionBusy}
                onClick={() => void previewMessageRedaction(line.id)}
              >
                {locale === "zh-CN" ? "清除敏感内容" : "Remove sensitive content"}
              </button>
            )}
            {line.actions && line.actions.length > 0 && (
              <div className="row wrap assistant-actions">
                {line.actions.map((action, index) => (
                  <button
                    key={`${action.kind}-${index}`}
                    className="btn sm"
                    disabled={skillBusy && action.kind === "draft_skill"}
                    onClick={() => void runAction(action, line.content)}
                  >
                    {actionLabel(action.kind, locale, skillBusy)}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="assistant-thinking">{locale === "zh-CN" ? "正在结合项目上下文思考…" : "Thinking with project context…"}</div>}
      </div>

      <div className="assistant-compose">
        <textarea
          rows={4}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
          placeholder={
            hasLlm
              ? locale === "zh-CN" ? "询问当前论文…（Enter 发送，Shift+Enter 换行）" : "Ask about this paper… (Enter to send)"
              : locale === "zh-CN" ? "请先在设置 → 模型中配置 LLM" : "Configure an LLM in Settings → Models first"
          }
          disabled={!hasLlm || busy}
        />
        <div className="row">
          <span className="dim grow">{project ? (locale === "zh-CN" ? "已附加项目上下文" : "project context attached") : (locale === "zh-CN" ? "通用对话" : "general chat")}</span>
          <button className="btn sm primary" disabled={!hasLlm || busy || !input.trim()} onClick={() => void send()}>
            {busy ? (locale === "zh-CN" ? "生成中…" : "Generating…") : (locale === "zh-CN" ? "发送" : "Send")}
          </button>
        </div>
      </div>

      {promptLibrary && (
        <PromptTemplatesDialog
          projectId={project?.id ?? ""}
          onClose={() => setPromptLibrary(false)}
          onUse={(template) => {
            setPromptLibrary(false);
            if (template.variables.length) setTemplateFill(template);
            else setInput(template.content);
          }}
        />
      )}
      {governanceOpen && (
        <AssistantGovernanceDialog
          key={project?.id ?? "workbench"}
          projectId={project?.id ?? ""}
          projectTitle={project?.title ?? ""}
          onClose={() => setGovernanceOpen(false)}
          onChanged={() => refreshThreads()}
        />
      )}
      {redactionPreview && (
        <MessageRedactionDialog
          preview={redactionPreview}
          busy={redactionBusy}
          onClose={() => !redactionBusy && setRedactionPreview(null)}
          onConfirm={executeMessageRedaction}
        />
      )}
      {templateFill && (
        <TemplateVariablesDialog
          template={templateFill}
          onClose={() => setTemplateFill(null)}
          onApply={(content) => {
            setInput(content);
            setTemplateFill(null);
          }}
        />
      )}
      {commitDraft !== null && (
        <LocalCommitDialog
          initialMessage={commitDraft}
          busy={commitBusy}
          onClose={() => !commitBusy && setCommitDraft(null)}
          onConfirm={commitLocalVersion}
        />
      )}
      {skillDraft && (
        <SkillDraftDialog
          draft={skillDraft}
          projectId={project?.id ?? ""}
          onChange={setSkillDraft}
          onClose={() => setSkillDraft(null)}
          onSaved={async () => {
            await loadSkills();
            setSkillDraft(null);
          }}
        />
      )}
    </aside>
  );
}

function MessageRedactionDialog({
  preview,
  busy,
  onClose,
  onConfirm,
}: {
  preview: AssistantMessageRedactionPreview;
  busy: boolean;
  onClose: () => void;
  onConfirm: (reason: string) => Promise<void>;
}) {
  const locale = useStore((s) => s.locale);
  const [reason, setReason] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <span>{locale === "zh-CN" ? "清除消息敏感内容" : "Remove sensitive message content"}</span>
          <button className="btn icon sm" disabled={busy} onClick={onClose} aria-label={locale === "zh-CN" ? "关闭消息清理" : "Close message redaction"}>×</button>
        </header>
        <div className="modal-body">
          <div className="callout danger">
            {locale === "zh-CN"
              ? "此操作不可恢复。消息正文、建议动作和调用元数据会被永久替换；只保留哈希、大小和清理时间作为审计证明。"
              : "This cannot be undone. The message text, suggested actions and call metadata will be permanently replaced; only a hash, sizes and the redaction time remain for audit."}
          </div>
          <dl className="assistant-governance-stats">
            <div><dt>{locale === "zh-CN" ? "角色" : "Role"}</dt><dd>{preview.role}</dd></div>
            <div><dt>{locale === "zh-CN" ? "字符" : "Characters"}</dt><dd>{preview.character_count.toLocaleString()}</dd></div>
            <div><dt>{locale === "zh-CN" ? "动作" : "Actions"}</dt><dd>{preview.actions_count}</dd></div>
            <div><dt>{locale === "zh-CN" ? "内容大小" : "Content size"}</dt><dd>{formatAssistantBytes(preview.estimated_bytes)}</dd></div>
          </dl>
          <div className="field">
            <label htmlFor="assistant-redaction-reason">{locale === "zh-CN" ? "清理原因（可选，最多 200 字符）" : "Reason (optional, up to 200 characters)"}</label>
            <input
              id="assistant-redaction-reason"
              value={reason}
              maxLength={200}
              disabled={busy}
              onChange={(event) => setReason(event.target.value)}
            />
          </div>
          <label className="check-row">
            <input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} />
            <span>{locale === "zh-CN" ? "我确认永久清除此消息的敏感内容" : "I confirm permanently removing this message's sensitive content"}</span>
          </label>
        </div>
        <footer>
          <button className="btn" disabled={busy} onClick={onClose}>{locale === "zh-CN" ? "取消" : "Cancel"}</button>
          <button className="btn danger" disabled={busy || !confirmed} onClick={() => void onConfirm(reason)}>
            {busy ? (locale === "zh-CN" ? "清理中…" : "Removing…") : (locale === "zh-CN" ? "永久清除" : "Remove permanently")}
          </button>
        </footer>
      </div>
    </div>
  );
}

function AssistantGovernanceDialog({
  projectId,
  projectTitle,
  onClose,
  onChanged,
}: {
  projectId: string;
  projectTitle: string;
  onClose: () => void;
  onChanged: () => Promise<void>;
}) {
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const [stats, setStats] = useState<AssistantScopeStats | null>(null);
  const [retentionDays, setRetentionDays] = useState(0);
  const [savedRetentionDays, setSavedRetentionDays] = useState(0);
  const [preview, setPreview] = useState<AssistantMaintenancePreview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [importArchive, setImportArchive] = useState<AssistantConversationExport | null>(null);
  const [importPreview, setImportPreview] = useState<AssistantImportPreview | null>(null);
  const [importConfirmed, setImportConfirmed] = useState(false);
  const [busy, setBusy] = useState<
    "load" | "settings" | "export" | "import-open" | "import" | "preview" | "delete" | ""
  >("load");
  const [exportPath, setExportPath] = useState("");

  const load = useCallback(async () => {
    setBusy("load");
    try {
      const [threads, settings] = await Promise.all([
        endpoints.assistant.threads(projectId),
        endpoints.settings.read(),
      ]);
      const days = Number(settings.assistant?.retention_days ?? 0);
      setStats(threads.stats);
      setRetentionDays(days);
      setSavedRetentionDays(days);
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "加载对话治理信息" : "loading conversation governance");
    } finally {
      setBusy("");
    }
  }, [locale, projectId, reportError]);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveRetention() {
    const days = Math.max(0, Math.min(3650, Math.round(retentionDays)));
    setBusy("settings");
    try {
      const settings = await endpoints.settings.update({ assistant: { retention_days: days } });
      const saved = Number(settings.assistant?.retention_days ?? 0);
      setRetentionDays(saved);
      setSavedRetentionDays(saved);
      setPreview(null);
      setConfirmed(false);
      notify({
        kind: "success",
        message: locale === "zh-CN" ? "对话保留策略已保存" : "Conversation retention policy saved",
      });
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "保存对话保留策略" : "saving conversation retention");
    } finally {
      setBusy("");
    }
  }

  async function exportConversations(compressed = false) {
    setBusy("export");
    setExportPath("");
    try {
      const payload = await endpoints.assistant.exportThreads(projectId);
      const date = new Date().toISOString().slice(0, 10);
      const name = projectId
        ? `${projectTitle || projectId}-assistant-conversations-${date}.json${compressed ? ".gz" : ""}`
        : `workbench-assistant-conversations-${date}.json${compressed ? ".gz" : ""}`;
      const bridge = window.papercreator;
      if (bridge) {
        const result = compressed
          ? await bridge.dialog.saveAssistantArchive({ suggestedName: name, data: payload, compressed: true })
          : await bridge.dialog.saveJson({ suggestedName: name, data: payload });
        if (result.canceled) return;
        setExportPath(result.path ?? "");
      } else {
        const source = new Blob(
          [`${JSON.stringify(payload, null, 2)}\n`],
          { type: "application/json" },
        );
        const blob = compressed
          ? await new Response(source.stream().pipeThrough(new CompressionStream("gzip"))).blob()
          : source;
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = name;
        anchor.click();
        URL.revokeObjectURL(url);
      }
      notify({
        kind: "success",
        message: locale === "zh-CN"
          ? (compressed ? "AI 对话压缩归档已导出" : "AI 对话已导出")
          : (compressed ? "Compressed AI conversation archive exported" : "AI conversations exported"),
      });
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "导出 AI 对话" : "exporting AI conversations");
    } finally {
      setBusy("");
    }
  }

  async function chooseImportArchive() {
    setBusy("import-open");
    setImportArchive(null);
    setImportPreview(null);
    setImportConfirmed(false);
    try {
      const selected = window.papercreator
        ? await window.papercreator.dialog.openAssistantArchive()
        : await openAssistantArchiveInBrowser();
      if (!selected || selected.canceled) return;
      if (!selected.data || typeof selected.data !== "object") {
        throw new Error(locale === "zh-CN" ? "归档内容不是 JSON 对象" : "Archive content is not a JSON object");
      }
      const archive = selected.data as AssistantConversationExport;
      const nextPreview = await endpoints.assistant.previewImport(projectId, archive);
      setImportArchive(archive);
      setImportPreview(nextPreview);
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "读取 AI 对话归档" : "reading AI conversation archive");
    } finally {
      setBusy("");
    }
  }

  async function executeImport() {
    if (!importArchive || !importPreview || !importConfirmed) return;
    setBusy("import");
    try {
      const result = await endpoints.assistant.executeImport(projectId, importArchive, importPreview);
      notify({
        kind: "success",
        message: locale === "zh-CN"
          ? `已导入 ${result.imported_threads} 个对话、${result.imported_messages} 条消息；跳过 ${result.skipped_threads} 个已有对话`
          : `Imported ${result.imported_threads} conversations and ${result.imported_messages} messages; skipped ${result.skipped_threads} existing conversations`,
      });
      setImportArchive(null);
      setImportPreview(null);
      setImportConfirmed(false);
      await Promise.all([load(), onChanged()]);
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "导入 AI 对话" : "importing AI conversations");
      setImportPreview(null);
      setImportConfirmed(false);
      await load();
    } finally {
      setBusy("");
    }
  }

  async function previewDeletion(mode: "all" | "retention") {
    setBusy("preview");
    setConfirmed(false);
    try {
      setPreview(await endpoints.assistant.previewMaintenance(
        projectId,
        mode,
        mode === "retention" ? savedRetentionDays : 0,
      ));
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "预览对话删除" : "previewing conversation deletion");
    } finally {
      setBusy("");
    }
  }

  async function executeDeletion() {
    if (!preview || !confirmed) return;
    setBusy("delete");
    try {
      const result = await endpoints.assistant.executeMaintenance(preview);
      notify({
        kind: "success",
        message: locale === "zh-CN"
          ? `已删除 ${result.deleted_threads} 个对话、${result.deleted_messages} 条消息`
          : `Deleted ${result.deleted_threads} conversations and ${result.deleted_messages} messages`,
      });
      setPreview(null);
      setConfirmed(false);
      await Promise.all([load(), onChanged()]);
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "删除 AI 对话" : "deleting AI conversations");
      setPreview(null);
      setConfirmed(false);
      await load();
    } finally {
      setBusy("");
    }
  }

  const scopeLabel = projectId
    ? (projectTitle || (locale === "zh-CN" ? "当前论文项目" : "Current paper project"))
    : (locale === "zh-CN" ? "工作台通用对话" : "Workbench conversations");
  const retentionEnabled = retentionDays > 0;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <span>{locale === "zh-CN" ? "AI 对话数据管理" : "AI conversation data"}</span>
          <button className="btn icon sm" disabled={Boolean(busy)} onClick={onClose} aria-label={locale === "zh-CN" ? "关闭对话数据管理" : "Close conversation data"}>×</button>
        </header>
        <div className="modal-body assistant-governance">
          <div>
            <strong>{scopeLabel}</strong>
            <div className="dim">{projectId ? (locale === "zh-CN" ? "范围：仅当前论文项目" : "Scope: current paper project only") : (locale === "zh-CN" ? "范围：未关联论文项目的工作台对话" : "Scope: workbench chats not linked to a paper project")}</div>
          </div>

          <dl className="assistant-governance-stats">
            <div><dt>{locale === "zh-CN" ? "对话" : "Conversations"}</dt><dd>{stats?.thread_count.toLocaleString() ?? "…"}</dd></div>
            <div><dt>{locale === "zh-CN" ? "消息" : "Messages"}</dt><dd>{stats?.message_count.toLocaleString() ?? "…"}</dd></div>
            <div><dt>{locale === "zh-CN" ? "字符" : "Characters"}</dt><dd>{stats?.character_count.toLocaleString() ?? "…"}</dd></div>
            <div><dt>{locale === "zh-CN" ? "内容大小" : "Content size"}</dt><dd>{stats ? formatAssistantBytes(stats.estimated_bytes) : "…"}</dd></div>
          </dl>
          {stats?.last_activity && (
            <div className="dim">
              {locale === "zh-CN" ? "最近活动" : "Last activity"}：{new Date(stats.last_activity).toLocaleString(locale)}
            </div>
          )}

          <div className="row wrap">
            <button className="btn" disabled={Boolean(busy)} onClick={() => void exportConversations(false)}>
              {busy === "export" ? (locale === "zh-CN" ? "导出中…" : "Exporting…") : (locale === "zh-CN" ? "导出范围内全部对话" : "Export all in scope")}
            </button>
            <button className="btn" disabled={Boolean(busy)} onClick={() => void exportConversations(true)}>
              {locale === "zh-CN" ? "导出压缩归档" : "Export compressed archive"}
            </button>
            <button className="btn" disabled={Boolean(busy)} onClick={() => void chooseImportArchive()}>
              {busy === "import-open"
                ? (locale === "zh-CN" ? "读取中…" : "Reading…")
                : (locale === "zh-CN" ? "导入对话归档" : "Import conversation archive")}
            </button>
            {exportPath && (
              <button className="btn" onClick={() => void window.papercreator?.shell.showItem(exportPath)}>
                {locale === "zh-CN" ? "在文件夹中显示" : "Show in folder"}
              </button>
            )}
          </div>

          {importPreview && (
            <div className="assistant-import-preview">
              <strong>{locale === "zh-CN" ? "导入预览" : "Import preview"}</strong>
              <div className="dim">
                {locale === "zh-CN" ? "来源" : "Source"}：
                {importPreview.source_scope.kind === "project"
                  ? (importPreview.source_scope.title || importPreview.source_scope.project_id)
                  : (locale === "zh-CN" ? "工作台通用对话" : "Workbench conversations")}
              </div>
              <div>
                {locale === "zh-CN"
                  ? `归档含 ${importPreview.stats.thread_count} 个对话、${importPreview.stats.message_count} 条消息；将新增 ${importPreview.stats.new_threads} 个，跳过 ${importPreview.stats.already_imported_threads} 个完全相同的已有对话。`
                  : `Archive contains ${importPreview.stats.thread_count} conversations and ${importPreview.stats.message_count} messages; ${importPreview.stats.new_threads} new and ${importPreview.stats.already_imported_threads} identical existing conversations will be skipped.`}
              </div>
              <div className="dim">
                {locale === "zh-CN"
                  ? "导入会生成本地副本；历史建议动作仍需重新确认，不会自动执行。"
                  : "Import creates local copies. Historical suggested actions remain unexecuted and require confirmation again."}
              </div>
              <label className="check-row">
                <input
                  type="checkbox"
                  checked={importConfirmed}
                  disabled={busy === "import" || importPreview.stats.new_threads === 0}
                  onChange={(event) => setImportConfirmed(event.target.checked)}
                />
                <span>{locale === "zh-CN" ? "我确认把新对话导入当前范围" : "I confirm importing new conversations into the current scope"}</span>
              </label>
              <div className="row">
                <button className="btn" disabled={busy === "import"} onClick={() => {
                  setImportArchive(null);
                  setImportPreview(null);
                  setImportConfirmed(false);
                }}>{locale === "zh-CN" ? "取消" : "Cancel"}</button>
                <button
                  className="btn primary"
                  disabled={!importConfirmed || busy === "import" || importPreview.stats.new_threads === 0}
                  onClick={() => void executeImport()}
                >
                  {busy === "import" ? (locale === "zh-CN" ? "导入中…" : "Importing…") : (locale === "zh-CN" ? "确认导入" : "Confirm import")}
                </button>
              </div>
            </div>
          )}

          <hr />
          <div className="field">
            <label className="check-row">
              <input
                type="checkbox"
                checked={retentionEnabled}
                disabled={Boolean(busy)}
                onChange={(event) => setRetentionDays(event.target.checked ? (savedRetentionDays || 90) : 0)}
              />
              <span>{locale === "zh-CN" ? "启用按最后活动时间清理" : "Enable cleanup by last activity"}</span>
            </label>
          </div>
          {retentionEnabled && (
            <div className="field">
              <label htmlFor="assistant-retention-days">{locale === "zh-CN" ? "保留最近天数" : "Keep recent days"}</label>
              <input
                id="assistant-retention-days"
                type="number"
                min={1}
                max={3650}
                step={1}
                value={retentionDays}
                disabled={Boolean(busy)}
                onChange={(event) => setRetentionDays(Number(event.target.value))}
              />
            </div>
          )}
          <div className="row wrap">
            <button
              className="btn"
              disabled={Boolean(busy) || retentionDays === savedRetentionDays || (retentionEnabled && (!Number.isFinite(retentionDays) || retentionDays < 1 || retentionDays > 3650))}
              onClick={() => void saveRetention()}
            >
              {busy === "settings" ? (locale === "zh-CN" ? "保存中…" : "Saving…") : (locale === "zh-CN" ? "保存保留策略" : "Save retention policy")}
            </button>
            <button
              className="btn"
              disabled={Boolean(busy) || savedRetentionDays < 1}
              onClick={() => void previewDeletion("retention")}
            >
              {locale === "zh-CN" ? "预览到期清理" : "Preview expired cleanup"}
            </button>
            <button className="btn danger" disabled={Boolean(busy)} onClick={() => void previewDeletion("all")}>
              {locale === "zh-CN" ? "预览全部删除" : "Preview delete all"}
            </button>
          </div>

          {preview && (
            <div className="assistant-delete-preview">
              <strong>
                {preview.mode === "all"
                  ? (locale === "zh-CN" ? "全部删除预览" : "Delete-all preview")
                  : (locale === "zh-CN" ? `超过 ${preview.older_than_days} 天的清理预览` : `Cleanup preview: inactive for ${preview.older_than_days} days`)}
              </strong>
              <div>
                {locale === "zh-CN"
                  ? `将删除 ${preview.stats.thread_count} 个对话、${preview.stats.message_count} 条消息（约 ${formatAssistantBytes(preview.stats.estimated_bytes)}）。`
                  : `Will delete ${preview.stats.thread_count} conversations and ${preview.stats.message_count} messages (about ${formatAssistantBytes(preview.stats.estimated_bytes)}).`}
              </div>
              <label className="check-row">
                <input type="checkbox" checked={confirmed} disabled={busy === "delete" || preview.stats.thread_count === 0} onChange={(event) => setConfirmed(event.target.checked)} />
                <span>{locale === "zh-CN" ? "我确认永久删除此预览中的对话" : "I confirm permanent deletion of the conversations in this preview"}</span>
              </label>
              <div className="row">
                <button className="btn" disabled={busy === "delete"} onClick={() => { setPreview(null); setConfirmed(false); }}>{locale === "zh-CN" ? "取消" : "Cancel"}</button>
                <button className="btn danger" disabled={!confirmed || busy === "delete" || preview.stats.thread_count === 0} onClick={() => void executeDeletion()}>
                  {busy === "delete" ? (locale === "zh-CN" ? "删除中…" : "Deleting…") : (locale === "zh-CN" ? "确认永久删除" : "Permanently delete")}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function formatAssistantBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
}

async function openAssistantArchiveInBrowser(): Promise<{
  canceled: boolean;
  path?: string;
  data?: unknown;
}> {
  const file = await new Promise<File | null>((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".json,.json.gz,application/json,application/gzip";
    input.onchange = () => resolve(input.files?.[0] ?? null);
    input.click();
  });
  if (!file) return { canceled: true };
  if (file.size > 256 * 1024 * 1024) {
    throw new Error("assistant archive exceeds the 256 MiB compressed limit");
  }
  const compressed = file.name.toLowerCase().endsWith(".gz");
  const text = compressed
    ? await new Response(file.stream().pipeThrough(new DecompressionStream("gzip"))).text()
    : await file.text();
  if (new Blob([text]).size > 256 * 1024 * 1024) {
    throw new Error("assistant archive exceeds the 256 MiB uncompressed limit");
  }
  return {
    canceled: false,
    path: file.name,
    data: JSON.parse(text.replace(/^\uFEFF/, "")),
  };
}

function actionLabel(kind: AssistantAction["kind"], locale: "zh-CN" | "en-US", busy: boolean) {
  const zh = {
    draft_skill: busy ? "生成 Skill 中…" : "生成 Skill 草稿",
    open_search: "打开文献检索",
    commit_local_version: "提交本地版本",
    insert_into_section: "追加到当前章节",
  };
  const en = {
    draft_skill: busy ? "Drafting Skill…" : "Draft a Skill",
    open_search: "Open literature search",
    commit_local_version: "Commit local version",
    insert_into_section: "Append to current section",
  };
  return (locale === "zh-CN" ? zh : en)[kind];
}

function TemplateVariablesDialog({
  template,
  onClose,
  onApply,
}: {
  template: PromptTemplate;
  onClose: () => void;
  onApply: (content: string) => void;
}) {
  const locale = useStore((s) => s.locale);
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(template.variables.map((variable) => [variable, ""])),
  );

  function apply() {
    let content = template.content;
    for (const variable of template.variables) {
      content = content.replace(
        new RegExp(`\\{\\{\\s*${escapeRegex(variable)}\\s*\\}\\}`, "g"),
        () => values[variable] ?? "",
      );
    }
    onApply(content);
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <span>{locale === "zh-CN" ? `填写“${template.name}”的变量` : `Fill variables for “${template.name}”`}</span>
          <button className="btn icon sm" onClick={onClose} aria-label={locale === "zh-CN" ? "关闭变量填写" : "Close variable form"}>×</button>
        </header>
        <div className="modal-body">
          {template.variables.map((variable, index) => {
            const id = `prompt-variable-${index}`;
            return (
              <div className="field" key={variable}>
                <label htmlFor={id}>{variable}</label>
                <input
                  id={id}
                  autoFocus={index === 0}
                  value={values[variable] ?? ""}
                  onChange={(event) => setValues({ ...values, [variable]: event.target.value })}
                />
              </div>
            );
          })}
        </div>
        <footer>
          <button className="btn" onClick={onClose}>{locale === "zh-CN" ? "取消" : "Cancel"}</button>
          <button className="btn primary" onClick={apply}>{locale === "zh-CN" ? "插入对话" : "Insert into chat"}</button>
        </footer>
      </div>
    </div>
  );
}

function LocalCommitDialog({
  initialMessage,
  busy,
  onClose,
  onConfirm,
}: {
  initialMessage: string;
  busy: boolean;
  onClose: () => void;
  onConfirm: (message: string) => Promise<void>;
}) {
  const locale = useStore((s) => s.locale);
  const [message, setMessage] = useState(initialMessage);
  const [confirmed, setConfirmed] = useState(false);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <header>
          <span>{locale === "zh-CN" ? "创建本地 Git 版本" : "Create a local Git version"}</span>
          <button className="btn icon sm" disabled={busy} onClick={onClose} aria-label={locale === "zh-CN" ? "关闭本地提交" : "Close local commit"}>×</button>
        </header>
        <div className="modal-body">
          <div className="field">
            <label htmlFor="assistant-local-commit-message">{locale === "zh-CN" ? "提交说明" : "Commit message"}</label>
            <input id="assistant-local-commit-message" autoFocus value={message} onChange={(event) => setMessage(event.target.value)} />
          </div>
          <label className="check-row">
            <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
            <span>{locale === "zh-CN" ? "我确认这只会写入当前论文项目的本地 Git，不会推送到网络。" : "I understand this writes only to this paper project's local Git and will not push to a remote."}</span>
          </label>
        </div>
        <footer>
          <button className="btn" disabled={busy} onClick={onClose}>{locale === "zh-CN" ? "取消" : "Cancel"}</button>
          <button className="btn primary" disabled={busy || !confirmed || !message.trim()} onClick={() => void onConfirm(message)}>
            {busy ? (locale === "zh-CN" ? "提交中…" : "Committing…") : (locale === "zh-CN" ? "创建本地提交" : "Create local commit")}
          </button>
        </footer>
      </div>
    </div>
  );
}

function PromptTemplatesDialog({
  projectId,
  onClose,
  onUse,
}: {
  projectId: string;
  onClose: () => void;
  onUse: (template: PromptTemplate) => void;
}) {
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const [items, setItems] = useState<PromptTemplate[]>([]);
  const [selected, setSelected] = useState<PromptTemplate | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", content: "", scope: projectId ? "project" : "workbench" });

  const load = useCallback(async () => {
    try {
      setItems((await endpoints.prompts.list(projectId)).items);
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "加载提示词模板" : "loading prompt templates");
    }
  }, [locale, projectId, reportError]);

  useEffect(() => {
    void load();
  }, [load]);
  const filtered = items.filter((item) => `${item.name} ${item.description} ${item.content}`.toLowerCase().includes(query.toLowerCase()));

  function edit(template: PromptTemplate | null) {
    setSelected(template);
    setForm(template
      ? { name: template.name, description: template.description, content: template.content, scope: template.scope }
      : { name: "", description: "", content: "", scope: projectId ? "project" : "workbench" });
  }

  async function save() {
    if (!form.name.trim() || !form.content.trim()) return;
    setBusy(true);
    const body = {
      name: form.name,
      description: form.description,
      content: form.content,
      project_id: form.scope === "project" ? projectId : "",
    };
    try {
      if (selected) await endpoints.prompts.update(selected.id, body);
      else await endpoints.prompts.create(body);
      notify({ kind: "success", message: locale === "zh-CN" ? "提示词模板已保存" : "Prompt template saved" });
      edit(null);
      await load();
    } catch (error) {
      reportError(error, locale === "zh-CN" ? "保存提示词模板" : "saving a prompt template");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal wide" onClick={(event) => event.stopPropagation()}>
        <header>
          <span>{locale === "zh-CN" ? "自定义提示词模板" : "Custom prompt templates"}</span>
          <button className="btn icon sm" onClick={onClose} aria-label={locale === "zh-CN" ? "关闭提示词模板" : "Close prompt templates"}>×</button>
        </header>
        <div className="modal-body prompt-template-layout">
          <div className="prompt-template-list">
            <div className="row">
              <input className="grow" aria-label={locale === "zh-CN" ? "搜索模板" : "Search templates"} value={query} onChange={(event) => setQuery(event.target.value)} placeholder={locale === "zh-CN" ? "搜索模板" : "Search templates"} />
              <button className="btn sm" onClick={() => edit(null)} aria-label={locale === "zh-CN" ? "新建提示词模板" : "New prompt template"}>＋</button>
            </div>
            {filtered.map((item) => (
              <button key={item.id} className={`prompt-template-item${selected?.id === item.id ? " active" : ""}`} onClick={() => edit(item)}>
                <strong>{item.name}</strong>
                <span className="dim">{item.scope === "project" ? (locale === "zh-CN" ? "当前项目" : "project") : (locale === "zh-CN" ? "工作台" : "workbench")}</span>
                <span className="truncate">{item.description || item.content}</span>
              </button>
            ))}
            {filtered.length === 0 && <div className="empty">{locale === "zh-CN" ? "暂无模板，点击＋新建或在右侧粘贴。" : "No templates. Click + or paste one on the right."}</div>}
          </div>
          <div className="prompt-template-editor">
            <div className="field"><label htmlFor="prompt-template-name">{locale === "zh-CN" ? "名称" : "Name"}</label><input id="prompt-template-name" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></div>
            <div className="field"><label htmlFor="prompt-template-description">{locale === "zh-CN" ? "说明" : "Description"}</label><input id="prompt-template-description" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></div>
            <div className="field">
              <label htmlFor="prompt-template-scope">{locale === "zh-CN" ? "作用域" : "Scope"}</label>
              <select id="prompt-template-scope" value={form.scope} onChange={(event) => setForm({ ...form, scope: event.target.value })}>
                <option value="workbench">{locale === "zh-CN" ? "整个工作台" : "Whole workbench"}</option>
                {projectId && <option value="project">{locale === "zh-CN" ? "当前论文项目" : "Current paper project"}</option>}
              </select>
            </div>
            <div className="field grow"><label htmlFor="prompt-template-content">{locale === "zh-CN" ? "模板内容（用 {{variable}} 声明变量）" : "Template content (variables use {{variable}})"}</label><textarea id="prompt-template-content" rows={14} value={form.content} onChange={(event) => setForm({ ...form, content: event.target.value })} /></div>
            <div className="row wrap">
              <button className="btn sm" onClick={async () => {
                try { setForm({ ...form, content: await navigator.clipboard.readText() }); }
                catch (error) { reportError(error, locale === "zh-CN" ? "读取剪贴板" : "reading the clipboard"); }
              }}>{locale === "zh-CN" ? "粘贴" : "Paste"}</button>
              <button className="btn sm" disabled={!form.content} onClick={async () => {
                try { await navigator.clipboard.writeText(form.content); notify({ kind: "success", message: locale === "zh-CN" ? "已复制" : "Copied" }); }
                catch (error) { reportError(error, locale === "zh-CN" ? "复制模板" : "copying the template"); }
              }}>{locale === "zh-CN" ? "复制" : "Copy"}</button>
              {selected && <button className="btn sm" onClick={() => onUse(selected)}>{locale === "zh-CN" ? "用于对话" : "Use in chat"}</button>}
              <div className="grow" />
              {selected && <button className="btn sm danger" onClick={async () => {
                if (!window.confirm(locale === "zh-CN" ? `删除“${selected.name}”？` : `Delete “${selected.name}”?`)) return;
                try { await endpoints.prompts.remove(selected.id); edit(null); await load(); }
                catch (error) { reportError(error, locale === "zh-CN" ? "删除提示词模板" : "deleting the prompt template"); }
              }}>{locale === "zh-CN" ? "删除" : "Delete"}</button>}
              <button className="btn sm primary" disabled={busy || !form.name.trim() || !form.content.trim()} onClick={() => void save()}>{busy ? (locale === "zh-CN" ? "保存中…" : "Saving…") : (locale === "zh-CN" ? "保存" : "Save")}</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function SkillDraftDialog({ draft, projectId, onChange, onClose, onSaved }: { draft: SkillDraft; projectId: string; onChange: (draft: SkillDraft) => void; onClose: () => void; onSaved: () => Promise<void> }) {
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const reportError = useStore((s) => s.reportError);
  const [busy, setBusy] = useState(false);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <header><span>{locale === "zh-CN" ? "审核 AI 生成的 Skill" : "Review AI-authored Skill"}</span><button className="btn icon sm" onClick={onClose} aria-label={locale === "zh-CN" ? "关闭 Skill 审核" : "Close skill review"}>×</button></header>
        <div className="modal-body">
          <div className="field"><label>{locale === "zh-CN" ? "名称" : "Name"}</label><input value={draft.name} onChange={(event) => onChange({ ...draft, name: event.target.value })} /></div>
          <div className="field"><label>{locale === "zh-CN" ? "说明" : "Description"}</label><input value={draft.description} onChange={(event) => onChange({ ...draft, description: event.target.value })} /></div>
          <div className="field"><label>{locale === "zh-CN" ? "指令（保存前必须审核）" : "Instructions (review before saving)"}</label><textarea rows={14} value={draft.instructions} onChange={(event) => onChange({ ...draft, instructions: event.target.value })} /></div>
          {draft.warnings.map((warning) => <div className="warn-text" key={warning}>⚠ {warning}</div>)}
          <p className="dim">{projectId ? (locale === "zh-CN" ? "将保存到当前论文项目，不影响其他项目。" : "Will be saved to this paper project only.") : (locale === "zh-CN" ? "将保存为当前工作台的用户 Skill。" : "Will be saved as a workbench user Skill.")}</p>
        </div>
        <footer><button className="btn" onClick={onClose}>{locale === "zh-CN" ? "取消" : "Cancel"}</button><button className="btn primary" disabled={busy || !draft.name.trim() || !draft.instructions.trim()} onClick={async () => {
          setBusy(true);
          try {
            await endpoints.skills.save({ ...draft, scope: projectId ? "project" : "user", project_id: projectId, overwrite: false });
            notify({ kind: "success", message: locale === "zh-CN" ? `Skill“${draft.name}”已保存` : `Skill “${draft.name}” saved` });
            await onSaved();
          } catch (error) { reportError(error, locale === "zh-CN" ? "保存 Skill" : "saving the Skill"); }
          finally { setBusy(false); }
        }}>{busy ? (locale === "zh-CN" ? "保存中…" : "Saving…") : (locale === "zh-CN" ? "确认并保存" : "Confirm and save")}</button></footer>
      </div>
    </div>
  );
}

function escapeRegex(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
