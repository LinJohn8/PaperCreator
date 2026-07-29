/**
 * Settings view: models, retrieval sources, analysis stack, Overleaf, diagnostics.
 *
 * Written as a diagnostic surface rather than a preferences dialog, because the
 * things that go wrong in this app are configuration problems: a missing API key,
 * a blocked model host, an absent optional package. Each panel therefore shows
 * *current state and the specific blocker*, not just editable values.
 *
 * API keys are never returned in readable form. A configured key round-trips as
 * `***set***`, which the backend interprets as "unchanged".
 */

import { useEffect, useState } from "react";

import * as endpoints from "../api/endpoints";
import type { ConfigurationSources } from "../api/types";
import { useStore } from "../state/store";

const MASK = "***set***";

export function SettingsView() {
  const health = useStore((s) => s.health);
  const refreshHealth = useStore((s) => s.refreshHealth);
  const locale = useStore((s) => s.locale);
  const [settings, setSettings] = useState<Record<string, any> | null>(null);
  const [sources, setSources] = useState<ConfigurationSources | null>(null);
  const [tab, setTab] = useState<"general" | "models" | "retrieval" | "analysis" | "writing" | "system">(
    "general",
  );

  useEffect(() => {
    void Promise.all([
      endpoints.settings.read(),
      endpoints.settings.sources().catch(() => null),
    ]).then(([nextSettings, nextSources]) => {
      setSettings(nextSettings);
      setSources(nextSources);
    }).catch(() => undefined);
  }, []);

  if (!settings || !health) {
    return (
      <div className="view">
        <h1>{locale === "zh-CN" ? "设置" : "Settings"}</h1>
        <p className="dim">{locale === "zh-CN" ? "加载中…" : "Loading…"}</p>
      </div>
    );
  }

  async function patch(body: Record<string, unknown>) {
    try {
      setSettings(await endpoints.settings.update(body));
      await refreshHealth();
      useStore.getState().notify({
        kind: "success",
        message: locale === "zh-CN" ? "设置已保存" : "Settings saved",
      });
    } catch (error) {
      useStore.getState().reportError(error, "saving settings");
    }
  }

  return (
    <div className="view">
      <h1>{locale === "zh-CN" ? "设置与诊断" : "Settings and diagnostics"}</h1>
      <div className="row wrap" style={{ marginBottom: 16 }}>
        {([
          ["general", locale === "zh-CN" ? "通用" : "General"],
          ["models", locale === "zh-CN" ? "模型" : "Models"],
          ["retrieval", locale === "zh-CN" ? "检索源" : "Sources"],
          ["analysis", locale === "zh-CN" ? "分析算法" : "Analysis"],
          ["writing", locale === "zh-CN" ? "写作与导出" : "Writing"],
          ["system", locale === "zh-CN" ? "系统" : "System"],
        ] as const).map(([id, label]) => (
          <button
            key={id}
            className={`btn${tab === id ? " primary" : ""}`}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "general" && <GeneralPanel sources={sources} />}
      {tab === "models" && <ModelsPanel settings={settings} onPatch={patch} />}
      {tab === "retrieval" && <RetrievalPanel settings={settings} onPatch={patch} />}
      {tab === "analysis" && <AnalysisPanel settings={settings} onPatch={patch} />}
      {tab === "writing" && <WritingPanel settings={settings} onPatch={patch} />}
      {tab === "system" && <SystemPanel />}
    </div>
  );
}

function GeneralPanel({ sources }: { sources: ConfigurationSources | null }) {
  const locale = useStore((s) => s.locale);
  const setLocale = useStore((s) => s.setLocale);
  const notify = useStore((s) => s.notify);

  const sourceLabels: Record<string, string> = locale === "zh-CN"
    ? {
        defaults: "内置默认",
        settings_file: "界面设置",
        secrets_file: "密钥文件",
        dotenv: ".env",
        environment: "启动环境",
      }
    : {
        defaults: "defaults",
        settings_file: "UI settings",
        secrets_file: "secret file",
        dotenv: ".env",
        environment: "launch environment",
      };

  return (
    <>
      <div className="card">
      <h3>{locale === "zh-CN" ? "界面语言" : "Interface language"}</h3>
      <p className="sub">
        {locale === "zh-CN"
          ? "控制整个 PaperCreator 的菜单、页面、提示和诊断语言。论文的写作语言在各项目中单独设置。"
          : "Controls menus, views, notifications and diagnostics across PaperCreator. Manuscript languages remain project-specific."}
      </p>
      <div className="field" style={{ maxWidth: 320 }}>
        <label htmlFor="application-display-language">
          {locale === "zh-CN" ? "应用显示语言" : "Application display language"}
        </label>
        <select
          id="application-display-language"
          value={locale}
          onChange={(event) => {
            const next = event.target.value as "zh-CN" | "en-US";
            void setLocale(next)
              .then(() =>
                notify({
                  kind: "success",
                  message:
                    next === "zh-CN"
                      ? "界面语言已切换为中文"
                      : "Interface language changed to English",
                }),
              )
              .catch(() => undefined);
          }}
        >
          <option value="zh-CN">简体中文</option>
          <option value="en-US">English</option>
        </select>
        <span className="hint">
          {locale === "zh-CN"
            ? "语言选择保存在当前工作台，重新启动后仍会恢复。"
            : "The language choice is stored in this workbench and restored after restart."}
        </span>
      </div>
      </div>

      {sources && (
        <div className="card">
          <div className="row">
            <h3 className="grow">{locale === "zh-CN" ? "配置来源" : "Configuration sources"}</h3>
            {sources.environment.override_fields.length > 0 && (
              <span className="chip warn">
                {locale === "zh-CN" ? "启动环境覆盖" : "launch overrides"}
              </span>
            )}
          </div>
          <p className="sub mono">
            {sources.precedence.map((source) => sourceLabels[source] ?? source).join("  →  ")}
          </p>
          {sources.environment.override_fields.length > 0 ? (
            <>
              <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
                {locale === "zh-CN"
                  ? "以下字段由启动器或进程环境最终决定；在界面保存的值会保留，但当前运行不会采用。"
                  : "These fields are controlled by the launcher or process environment. UI values remain saved but are not effective in this run."}
              </p>
              <div className="row wrap">
                {sources.environment.override_fields.map((field) => (
                  <span className="chip mono" key={field}>{field}</span>
                ))}
              </div>
            </>
          ) : (
            <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
              {locale === "zh-CN"
                ? "当前没有启动环境覆盖字段。"
                : "No fields are overridden by the launch environment."}
            </p>
          )}
          {sources.dotenv.override_fields.length > 0 && (
            <p className="hint mono" style={{ marginTop: 10 }}>
              .env: {sources.dotenv.override_fields.join(", ")}
            </p>
          )}
          <p className="hint mono" style={{ marginTop: 10 }}>
            settings: {sources.settings_file.path}<br />
            secrets: {sources.secrets_file.path}
          </p>
        </div>
      )}
    </>
  );
}

function ModelsPanel({
  settings,
  onPatch,
}: {
  settings: Record<string, any>;
  onPatch: (body: Record<string, unknown>) => Promise<void>;
}) {
  const locale = useStore((s) => s.locale);
  const health = useStore((s) => s.health)!;
  const [providers, setProviders] = useState<any[]>([]);
  const [testing, setTesting] = useState("");
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    void endpoints.settings
      .llmProviders(false)
      .then((result) => setProviders(result.providers))
      .catch(() => undefined);
  }, [settings]);

  return (
    <>
      <div className="card">
        <h3>{locale === "zh-CN" ? "模型分工" : "Model roles"}</h3>
        <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
          {locale === "zh-CN"
            ? "格式为 provider:model。chat 用于写作与推理，fast 用于逐篇读论文等高频低判断任务，embedding 用于生成向量（可留空，改用本地模型）。"
            : "Format is provider:model. chat does the writing and reasoning; fast handles high-volume low-judgement work like per-paper notes; embedding is optional (a local model is usually better)."}
        </p>
        {(["default_chat", "default_fast", "default_embedding"] as const).map((role) => (
          <div key={role} className="field">
            <label>{role.replace("default_", "")}</label>
            <input
              defaultValue={settings.llm?.[role] ?? ""}
              placeholder={
                role === "default_chat"
                  ? "openai:gpt-4o  ·  anthropic:claude-sonnet-4-5  ·  ollama:qwen2.5"
                  : role === "default_fast"
                    ? "openai:gpt-4o-mini  ·  deepseek:deepseek-chat"
                    : "openai:text-embedding-3-small"
              }
              onBlur={(event) => {
                if (event.target.value !== (settings.llm?.[role] ?? "")) {
                  void onPatch({ llm: { [role]: event.target.value } });
                }
              }}
            />
          </div>
        ))}
        <div className="row wrap">
          <div className="field" style={{ width: 140, marginBottom: 0 }}>
            <label>{locale === "zh-CN" ? "生成温度（temperature）" : "temperature"}</label>
            <input
              type="number"
              step="0.05"
              defaultValue={settings.llm?.temperature ?? 0.4}
              onBlur={(event) =>
                void onPatch({ llm: { temperature: Number(event.target.value) } })
              }
            />
          </div>
          <div className="field" style={{ width: 160, marginBottom: 0 }}>
            <label>{locale === "zh-CN" ? "最大输出令牌数" : "max output tokens"}</label>
            <input
              type="number"
              defaultValue={settings.llm?.max_output_tokens ?? 4096}
              onBlur={(event) =>
                void onPatch({ llm: { max_output_tokens: Number(event.target.value) } })
              }
            />
          </div>
          <div className="field" style={{ width: 190, marginBottom: 0 }}>
            <label title="Hard ceiling per agent run; the run stops cleanly when reached">
              run token budget
            </label>
            <input
              type="number"
              defaultValue={settings.llm?.run_token_budget ?? 400000}
              onBlur={(event) =>
                void onPatch({ llm: { run_token_budget: Number(event.target.value) } })
              }
            />
          </div>
        </div>
      </div>

      <div className="row">
        <h2 className="grow">{locale === "zh-CN" ? "提供方" : "Providers"}</h2>
        <button className="btn sm" onClick={() => setAdding(!adding)}>
          {locale === "zh-CN" ? "添加提供方" : "Add provider"}
        </button>
      </div>

      {adding && <AddProviderForm onDone={() => setAdding(false)} onPatch={onPatch} />}

      {providers.length === 0 && (
        <div className="card">
          <p className="muted">
            {locale === "zh-CN"
              ? "尚未配置任何模型提供方。最省事的两种方式：填一个 API key，或在本机装 Ollama（免费、数据不外传）。"
              : "No provider configured yet. The two easiest paths: paste an API key, or install Ollama locally (free, and nothing leaves your machine)."}
          </p>
        </div>
      )}

      {providers.map((provider) => (
        <div key={provider.id} className="card">
          <div className="row">
            <span className="grow" style={{ fontWeight: 600 }}>
              {provider.label || provider.id}{" "}
              <span className="dim mono">({provider.kind})</span>
            </span>
            <span className={`chip ${provider.has_key || !provider.needs_key ? "ok" : "err"}`}>
              {provider.has_key
                ? locale === "zh-CN" ? "已配置密钥" : "key set"
                : provider.needs_key
                  ? locale === "zh-CN" ? "缺少密钥" : "no key"
                  : locale === "zh-CN" ? "无需密钥" : "no key needed"}
            </span>
            {health.llm.usable.includes(provider.id) && (
              <span className="chip on">{locale === "zh-CN" ? "可用" : "usable"}</span>
            )}
          </div>
          <div className="row wrap" style={{ marginTop: 8 }}>
            <div className="field grow" style={{ marginBottom: 0, minWidth: 240 }}>
              <label>{locale === "zh-CN" ? "基础地址（base URL）" : "base URL"}</label>
              <input
                defaultValue={provider.base_url}
                onBlur={(event) =>
                  void endpoints.settings.upsertLlmProvider(provider.id, {
                    ...provider,
                    api_key: MASK,
                    base_url: event.target.value,
                  })
                }
              />
            </div>
            <div className="field" style={{ marginBottom: 0, width: 230 }}>
              <label>{locale === "zh-CN" ? "默认模型" : "default model"}</label>
              <input
                defaultValue={provider.default_model}
                onBlur={(event) =>
                  void endpoints.settings.upsertLlmProvider(provider.id, {
                    ...provider,
                    api_key: MASK,
                    default_model: event.target.value,
                  })
                }
              />
            </div>
            {provider.needs_key && (
              <div className="field" style={{ marginBottom: 0, width: 240 }}>
                <label>{locale === "zh-CN" ? "API 密钥" : "API key"}</label>
                <input
                  type="password"
                  placeholder={provider.has_key
                    ? locale === "zh-CN" ? "••••••••（保持不变）" : "•••••••• (unchanged)"
                    : locale === "zh-CN" ? "粘贴密钥" : "paste key"}
                  onBlur={(event) => {
                    if (!event.target.value) return;
                    void endpoints.settings
                      .upsertLlmProvider(provider.id, {
                        ...provider,
                        api_key: event.target.value,
                      })
                      .then(() => {
                        event.target.value = "";
                        useStore.getState().notify({ kind: "success", message: locale === "zh-CN" ? "密钥已安全保存" : "Key stored" });
                        void useStore.getState().refreshHealth();
                      });
                  }}
                />
              </div>
            )}
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button
              className="btn sm"
              disabled={testing === provider.id}
              onClick={async () => {
                setTesting(provider.id);
                try {
                  const result = await endpoints.settings.testLlm(provider.id);
                  useStore.getState().notify({
                    kind: result.ok ? "success" : "error",
                    message: result.ok
                      ? locale === "zh-CN" ? `${provider.id} 连接正常（${result.duration_ms}ms）` : `${provider.id} works (${result.duration_ms}ms)`
                      : locale === "zh-CN"
                        ? `${provider.id} 连接失败${result.outcome ? `：${result.outcome.replaceAll("_", " ")}` : ""}`
                        : `${provider.id} failed${result.outcome ? `: ${result.outcome.replaceAll("_", " ")}` : ""}`,
                    detail: result.ok
                      ? locale === "zh-CN" ? `返回：${result.reply}` : `replied: ${result.reply}`
                      : [result.error, result.hint].filter(Boolean).join(" — "),
                  });
                } finally {
                  setTesting("");
                }
              }}
            >
              {testing === provider.id
                ? locale === "zh-CN" ? "测试中…" : "Testing…"
                : locale === "zh-CN" ? "测试连接" : "Test"}
            </button>
            {provider.models?.length > 0 && (
              <span className="dim truncate">
                {provider.models.slice(0, 6).join(", ")}
                {provider.models.length > 6 ? ` +${provider.models.length - 6}` : ""}
              </span>
            )}
          </div>
        </div>
      ))}
    </>
  );
}

function AddProviderForm({
  onDone,
  onPatch,
}: {
  onDone: () => void;
  onPatch: (body: Record<string, unknown>) => Promise<void>;
}) {
  const locale = useStore((s) => s.locale);
  const [form, setForm] = useState({
    id: "",
    kind: "openai",
    label: "",
    base_url: "",
    api_key: "",
    default_model: "",
  });

  const PRESETS: Record<string, { base_url: string; model: string; kind: string }> = {
    openai: { base_url: "https://api.openai.com/v1", model: "gpt-4o-mini", kind: "openai" },
    deepseek: { base_url: "https://api.deepseek.com/v1", model: "deepseek-chat", kind: "openai" },
    openrouter: {
      base_url: "https://openrouter.ai/api/v1",
      model: "openai/gpt-4o-mini",
      kind: "openai",
    },
    anthropic: {
      base_url: "https://api.anthropic.com",
      model: "claude-sonnet-4-5",
      kind: "anthropic",
    },
    gemini: {
      base_url: "https://generativelanguage.googleapis.com",
      model: "gemini-2.0-flash",
      kind: "gemini",
    },
    ollama: { base_url: "http://127.0.0.1:11434", model: "qwen2.5", kind: "ollama" },
  };

  return (
    <div className="card">
      <h3>{locale === "zh-CN" ? "添加模型提供方" : "Add a provider"}</h3>
      <div className="row wrap" style={{ marginBottom: 10 }}>
        {Object.entries(PRESETS).map(([id, preset]) => (
          <button
            key={id}
            className="btn sm"
            onClick={() =>
              setForm({
                id,
                kind: preset.kind,
                label: id,
                base_url: preset.base_url,
                api_key: "",
                default_model: preset.model,
              })
            }
          >
            {id}
          </button>
        ))}
      </div>
      <div className="row wrap">
        <div className="field" style={{ width: 150 }}>
          <label>{locale === "zh-CN" ? "提供方 ID" : "provider id"}</label>
          <input
            value={form.id}
            onChange={(event) => setForm({ ...form, id: event.target.value })}
          />
        </div>
        <div className="field" style={{ width: 130 }}>
          <label>{locale === "zh-CN" ? "接口类型" : "kind"}</label>
          <select
            value={form.kind}
            onChange={(event) => setForm({ ...form, kind: event.target.value })}
          >
            <option value="openai">openai-compatible</option>
            <option value="anthropic">anthropic</option>
            <option value="gemini">gemini</option>
            <option value="ollama">ollama</option>
          </select>
        </div>
        <div className="field grow" style={{ minWidth: 220 }}>
          <label>{locale === "zh-CN" ? "基础地址（base URL）" : "base URL"}</label>
          <input
            value={form.base_url}
            onChange={(event) => setForm({ ...form, base_url: event.target.value })}
          />
        </div>
      </div>
      <div className="row wrap">
        <div className="field grow" style={{ minWidth: 200 }}>
          <label>{locale === "zh-CN" ? "默认模型" : "default model"}</label>
          <input
            value={form.default_model}
            onChange={(event) => setForm({ ...form, default_model: event.target.value })}
          />
        </div>
        <div className="field grow" style={{ minWidth: 200 }}>
          <label>
            {locale === "zh-CN" ? "API 密钥" : "API key"}{" "}
            {form.kind === "ollama" ? (locale === "zh-CN" ? "（不需要）" : "(not needed)") : ""}
          </label>
          <input
            type="password"
            value={form.api_key}
            disabled={form.kind === "ollama"}
            onChange={(event) => setForm({ ...form, api_key: event.target.value })}
          />
        </div>
      </div>
      <div className="row">
        <button
          className="btn primary"
          disabled={!form.id.trim()}
          onClick={async () => {
            await endpoints.settings.upsertLlmProvider(form.id, form);
            await onPatch({});
            onDone();
          }}
        >
          {locale === "zh-CN" ? "添加" : "Add"}
        </button>
        <button className="btn" onClick={onDone}>
          {locale === "zh-CN" ? "取消" : "Cancel"}
        </button>
      </div>
    </div>
  );
}

function RetrievalPanel({
  settings,
  onPatch,
}: {
  settings: Record<string, any>;
  onPatch: (body: Record<string, unknown>) => Promise<void>;
}) {
  const locale = useStore((s) => s.locale);
  const providers = useStore((s) => s.providers);
  const loadProviders = useStore((s) => s.loadProviders);
  const enabled: string[] = settings.retrieval?.enabled_providers ?? [];

  return (
    <>
      <div className="card">
        <h3>{locale === "zh-CN" ? "身份标识" : "Identity"}</h3>
        <div className="field">
          <label>{locale === "zh-CN" ? "联系邮箱" : "Contact email"}</label>
          <input
            defaultValue={settings.identity?.contact_email ?? ""}
            placeholder="you@university.edu"
            onBlur={(event) =>
              void onPatch({ identity: { contact_email: event.target.value } })
            }
          />
          <span className="hint">
            {locale === "zh-CN"
              ? "Crossref 与 OpenAlex 对标明身份的客户端提供更好的服务配额，强烈建议填写。不会用于其他用途。"
              : "Crossref and OpenAlex give identified clients better service. Strongly recommended; used for nothing else."}
          </span>
        </div>
      </div>

      <div className="card">
        <h3>{locale === "zh-CN" ? "默认启用的检索源" : "Enabled by default"}</h3>
        <div className="row wrap" style={{ gap: 5 }}>
          {providers.map((provider) => (
            <button
              key={provider.id}
              className={`chip clickable${enabled.includes(provider.id) ? " on" : ""}`}
              title={provider.unavailable_reason || provider.description}
              onClick={async () => {
                const next = enabled.includes(provider.id)
                  ? enabled.filter((id) => id !== provider.id)
                  : [...enabled, provider.id];
                if (!next.length) {
                  useStore.getState().notify({
                    kind: "warning",
                    message:
                      locale === "zh-CN"
                        ? "至少需要保留一个默认检索源"
                        : "Keep at least one retrieval source enabled",
                  });
                  return;
                }
                await onPatch({ retrieval: { enabled_providers: next } });
                await loadProviders();
              }}
            >
              {locale === "zh-CN" && provider.name_zh ? provider.name_zh : provider.name}
            </button>
          ))}
        </div>
      </div>

      <h2>{locale === "zh-CN" ? "各源的 API key" : "Provider API keys"}</h2>
      <p className="sub">
        {locale === "zh-CN"
          ? "全部为可选。没有 key 的源会自动降级或跳过，不会导致检索失败。"
          : "All optional. A source without a key either degrades or is skipped — a search never fails because of it."}
      </p>
      {providers
        .filter((provider) => provider.key_setting)
        .map((provider) => (
          <div key={provider.id} className="card">
            <div className="row">
              <span className="grow" style={{ fontWeight: 600 }}>
                {provider.name}
              </span>
              <span className={`chip ${provider.has_key ? "ok" : ""}`}>
                {provider.has_key
                  ? locale === "zh-CN" ? "已配置" : "configured"
                  : locale === "zh-CN" ? "未配置" : "not set"}
              </span>
            </div>
            <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
              {provider.unavailable_reason ||
                (locale === "zh-CN" && provider.description_zh
                  ? provider.description_zh
                  : provider.description)}
            </p>
            <div className="row">
              <input
                type="password"
                className="grow"
                placeholder={provider.has_key ? "••••••••  (unchanged)" : "paste key"}
                onBlur={(event) => {
                  if (!event.target.value) return;
                  void onPatch({
                    provider_keys: { [provider.key_setting]: event.target.value },
                  }).then(() => {
                    event.target.value = "";
                    void loadProviders();
                  });
                }}
              />
              {provider.signup_url && (
                <button
                  className="btn sm"
                  onClick={() =>
                    void window.papercreator?.shell.openExternal(provider.signup_url)
                  }
                >
                  {locale === "zh-CN" ? "申请 key" : "Get a key"}
                </button>
              )}
            </div>
          </div>
        ))}
    </>
  );
}

function AnalysisPanel({
  settings,
  onPatch,
}: {
  settings: Record<string, any>;
  onPatch: (body: Record<string, unknown>) => Promise<void>;
}) {
  const locale = useStore((s) => s.locale);
  const health = useStore((s) => s.health)!;
  const [probing, setProbing] = useState(false);
  const [probe, setProbe] = useState<Record<string, any> | null>(null);

  return (
    <>
      <div className="card">
        <h3>{locale === "zh-CN" ? "向量后端" : "Embedding backend"}</h3>
        <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
          {locale === "zh-CN"
            ? "这是决定图谱质量的关键：语义向量能识别「同义不同词」，词频向量只能看词面重合。"
            : "This determines landscape quality: semantic vectors capture meaning, lexical ones only shared vocabulary."}
        </p>
        {health.analysis.embedding_backends.map((backend) => (
          <div key={backend.id} className="row" style={{ padding: "4px 0", alignItems: "flex-start" }}>
            <span className={`dot ${backend.available ? "open" : "closed"}`} style={{ marginTop: 6 }} />
            <div className="grow">
              <div>
                {backend.name}{" "}
                <span
                  className={`chip ${
                    backend.quality === "high" ? "ok" : backend.quality === "low" ? "err" : "warn"
                  }`}
                >
                  {backend.quality}
                </span>
                {!backend.portable && (
                  <span
                    className="chip"
                    title="Vectors are fitted per corpus, so a new paper cannot be placed into an existing map without refitting"
                  >
                    corpus-relative
                  </span>
                )}
              </div>
              <div className="dim" style={{ fontSize: "var(--fs-xs)" }}>
                {backend.note}
              </div>
              {backend.blocker && (
                <div className="warn-text" style={{ fontSize: "var(--fs-xs)" }}>
                  ⚠ {backend.blocker}
                </div>
              )}
              {!backend.available && backend.requirement && (
                <div className="mono dim" style={{ fontSize: "var(--fs-xs)" }}>
                  {backend.requirement}
                </div>
              )}
            </div>
          </div>
        ))}
        <div className="field" style={{ marginTop: 12 }}>
          <label>{locale === "zh-CN" ? "选择后端" : "Backend"}</label>
          <select
            defaultValue={settings.analysis?.embedding_backend ?? "auto"}
            onChange={(event) =>
              void onPatch({ analysis: { embedding_backend: event.target.value } })
            }
          >
            <option value="auto">{locale === "zh-CN" ? "自动（选择最佳可用项）" : "auto (best available)"}</option>
            {health.analysis.embedding_backends.map((backend) => (
              <option key={backend.id} value={backend.id} disabled={!backend.available}>
                {backend.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="card">
        <h3>{locale === "zh-CN" ? "模型下载源" : "Model download host"}</h3>
        <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
          {locale === "zh-CN"
            ? "本地语义模型需要从 Hugging Face 下载一次（约 90MB）。若 huggingface.co 无法访问，填入镜像地址；修改后需要重启后端才生效。"
            : "The local semantic model downloads once from Hugging Face (~90MB). If huggingface.co is unreachable, set a mirror — the backend must be restarted for it to take effect."}
        </p>
        <div className="field">
          <label>HF_ENDPOINT</label>
          <input
            defaultValue={settings.analysis?.hf_endpoint ?? ""}
            placeholder="https://hf-mirror.com"
            onBlur={(event) => void onPatch({ analysis: { hf_endpoint: event.target.value } })}
          />
          <span className="hint">
            {locale === "zh-CN"
              ? "另一种可靠做法：手动下载模型文件夹，放到 <PAPERCREATOR_HOME>/models/all-MiniLM-L6-v2/，程序会直接从本地加载，完全不联网。"
              : "A reliable alternative: download the model folder by hand into <PAPERCREATOR_HOME>/models/all-MiniLM-L6-v2/ — it is then loaded from disk with no network at all."}
          </span>
        </div>
        <div className="row">
          <button
            className="btn sm"
            disabled={probing}
            onClick={async () => {
              setProbing(true);
              try {
                setProbe(await endpoints.settings.probeModelHost());
              } finally {
                setProbing(false);
              }
            }}
          >
            {probing
              ? locale === "zh-CN" ? "检测中…" : "Checking…"
              : locale === "zh-CN" ? "检测可达性" : "Check reachability"}
          </button>
          {probe && (
            <span className={probe.reachable ? "ok-text" : "err-text"}>
              {String(probe.endpoint)} — {probe.reachable ? "reachable" : "unreachable"}
              {probe.model_cached ? " · model already downloaded" : ""}
            </span>
          )}
        </div>
        {probe?.hint && <p className="warn-text">{String(probe.hint)}</p>}
      </div>

      <div className="card">
        <h3>{locale === "zh-CN" ? "降维与聚类" : "Projection and clustering"}</h3>
        <div className="row wrap">
          <div className="field" style={{ width: 190 }}>
            <label>{locale === "zh-CN" ? "降维方法" : "Reducer"}</label>
            <select
              defaultValue={settings.analysis?.reducer ?? "auto"}
              onChange={(event) => void onPatch({ analysis: { reducer: event.target.value } })}
            >
              <option value="auto">{locale === "zh-CN" ? "自动" : "auto"}</option>
              {health.analysis.reducers.map((reducer) => (
                <option key={reducer.id} value={reducer.id} disabled={!reducer.available}>
                  {reducer.name}
                  {reducer.supports_new_points ? "" : " (no incremental add)"}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ width: 190 }}>
            <label>{locale === "zh-CN" ? "聚类方法" : "Clusterer"}</label>
            <select
              defaultValue={settings.analysis?.clusterer ?? "auto"}
              onChange={(event) => void onPatch({ analysis: { clusterer: event.target.value } })}
            >
              <option value="auto">{locale === "zh-CN" ? "自动" : "auto"}</option>
              {health.analysis.clusterers.map((clusterer) => (
                <option key={clusterer.id} value={clusterer.id} disabled={!clusterer.available}>
                  {clusterer.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ width: 130 }}>
            <label title={locale === "zh-CN" ? "UMAP：值越大越强调全局结构" : "UMAP: larger values emphasise global structure"}>{locale === "zh-CN" ? "邻居数（n_neighbors）" : "n_neighbors"}</label>
            <input
              type="number"
              defaultValue={settings.analysis?.n_neighbors ?? 15}
              onBlur={(event) =>
                void onPatch({ analysis: { n_neighbors: Number(event.target.value) } })
              }
            />
          </div>
          <div className="field" style={{ width: 150 }}>
            <label>{locale === "zh-CN" ? "最小聚类大小" : "min_cluster_size"}</label>
            <input
              type="number"
              defaultValue={settings.analysis?.min_cluster_size ?? 5}
              onBlur={(event) =>
                void onPatch({ analysis: { min_cluster_size: Number(event.target.value) } })
              }
            />
          </div>
          <div className="field" style={{ width: 130 }}>
            <label>{locale === "zh-CN" ? "热力图网格" : "heatmap grid"}</label>
            <input
              type="number"
              defaultValue={settings.analysis?.heatmap_grid ?? 40}
              onBlur={(event) =>
                void onPatch({ analysis: { heatmap_grid: Number(event.target.value) } })
              }
            />
          </div>
        </div>
      </div>

      <div className="card">
        <h3>{locale === "zh-CN" ? "可选依赖状态" : "Optional packages"}</h3>
        <div className="row wrap">
          {Object.entries(health.analysis.optional_stack_installed).map(([name, installed]) => (
            <span key={name} className={`chip ${installed ? "ok" : "warn"}`}>
              {name} {installed ? "✓" : "✗"}
            </span>
          ))}
        </div>
        <p className="dim mono" style={{ fontSize: "var(--fs-xs)", marginTop: 8 }}>
          pip install "papercreator[analysis]"
        </p>
      </div>
    </>
  );
}

function WritingPanel({
  settings,
  onPatch,
}: {
  settings: Record<string, any>;
  onPatch: (body: Record<string, unknown>) => Promise<void>;
}) {
  const locale = useStore((s) => s.locale);
  const health = useStore((s) => s.health)!;

  return (
    <>
      <div className="card">
        <h3>{locale === "zh-CN" ? "写作默认值" : "Writing defaults"}</h3>
        <div className="row wrap">
          <div className="field" style={{ width: 160 }}>
            <label>{locale === "zh-CN" ? "默认语言" : "Default language"}</label>
            <select
              defaultValue={settings.writing?.default_language ?? "en"}
              onChange={(event) =>
                void onPatch({ writing: { default_language: event.target.value } })
              }
            >
              <option value="en">English</option>
              <option value="zh">中文</option>
            </select>
          </div>
          <div className="field" style={{ width: 160 }}>
            <label>{locale === "zh-CN" ? "引用格式" : "Citation style"}</label>
            <select
              defaultValue={settings.writing?.citation_style ?? "ieee"}
              onChange={(event) =>
                void onPatch({ writing: { citation_style: event.target.value } })
              }
            >
              {health.export.citation_styles.map((style) => (
                <option key={style} value={style}>
                  {style}
                </option>
              ))}
            </select>
          </div>
          <label className="row" style={{ gap: 6, marginTop: 18 }}>
            <input
              type="checkbox"
              defaultChecked={settings.writing?.bilingual ?? true}
              onChange={(event) => void onPatch({ writing: { bilingual: event.target.checked } })}
            />
            {locale === "zh-CN" ? "默认中英对照" : "Bilingual by default"}
          </label>
          <label className="row" style={{ gap: 6, marginTop: 18 }}>
            <input
              type="checkbox"
              defaultChecked={settings.writing?.auto_git_commit ?? true}
              onChange={(event) =>
                void onPatch({ writing: { auto_git_commit: event.target.checked } })
              }
            />
            {locale === "zh-CN" ? "重要变更后自动提交" : "Auto-commit after significant changes"}
          </label>
        </div>
      </div>

      <div className="card">
        <h3>{locale === "zh-CN" ? "导出能力" : "Export capabilities"}</h3>
        <div className="row wrap">
          <span className={`chip ${health.export.pandoc ? "ok" : "warn"}`}>
            pandoc {health.export.pandoc ? "✓" : "✗ (built-in writer used)"}
          </span>
          {Object.entries(health.export.latex_engines).map(([engine, available]) => (
            <span key={engine} className={`chip ${available ? "ok" : ""}`}>
              {engine} {available ? "✓" : "✗"}
            </span>
          ))}
          <span className={`chip ${health.export.can_build_pdf ? "ok" : "warn"}`}>
            {health.export.can_build_pdf
              ? locale === "zh-CN" ? "可本地生成 PDF" : "can build PDF locally"
              : locale === "zh-CN" ? "无法本地生成 PDF" : "cannot build PDF locally"}
          </span>
        </div>
        {!health.export.can_build_pdf && (
          <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
            {locale === "zh-CN"
              ? "安装 MiKTeX 或 TeX Live 即可本地编译；否则导出 LaTeX 项目上传 Overleaf 也一样可用。"
              : "Install MiKTeX or TeX Live to compile locally — or just export the LaTeX project and compile it on Overleaf."}
          </p>
        )}
      </div>

      <div className="card">
        <h3>Overleaf</h3>
        <p className="muted" style={{ fontSize: "var(--fs-sm)" }}>
          {locale === "zh-CN"
            ? "git 桥接是 Overleaf 付费功能。在 Overleaf 项目里 Menu > Git 复制地址，再到账号设置生成 token。"
            : "The git bridge is an Overleaf paid feature. Copy the URL from Menu > Git in your project, and generate a token in your account settings."}
        </p>
        <div className="field">
          <label>{locale === "zh-CN" ? "Git 地址" : "git URL"}</label>
          <input
            defaultValue={settings.overleaf?.git_url ?? ""}
            placeholder="https://git.overleaf.com/your-project-id"
            onBlur={(event) => void onPatch({ overleaf: { git_url: event.target.value } })}
          />
        </div>
        <div className="field">
          <label>{locale === "zh-CN" ? "Git 访问令牌" : "git token"}</label>
          <input
            type="password"
            placeholder={
              settings.overleaf?.git_token === MASK ? "••••••••  (unchanged)" : "paste token"
            }
            onBlur={(event) => {
              if (!event.target.value) return;
              void onPatch({ overleaf: { git_token: event.target.value } }).then(() => {
                event.target.value = "";
              });
            }}
          />
        </div>
      </div>
    </>
  );
}

function SystemPanel() {
  const health = useStore((s) => s.health)!;
  const refreshHealth = useStore((s) => s.refreshHealth);
  const locale = useStore((s) => s.locale);
  const notify = useStore((s) => s.notify);
  const [usage, setUsage] = useState<Record<string, any> | null>(null);
  const [cache, setCache] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    void endpoints.system.usage(30).then(setUsage).catch(() => undefined);
    void endpoints.system.cache().then(setCache).catch(() => undefined);
  }, []);

  const totals = (usage?.totals ?? {}) as Record<string, number>;

  return (
    <>
      <div className="card">
        <h3>{locale === "zh-CN" ? "运行状态" : "Status"}</h3>
        <dl className="kv">
          <dt>{locale === "zh-CN" ? "版本" : "version"}</dt>
          <dd>{health.version}</dd>
          <dt>{locale === "zh-CN" ? "运行时间" : "uptime"}</dt>
          <dd>{Math.round(health.uptime_s)}s</dd>
          <dt>{locale === "zh-CN" ? "数据库" : "database"}</dt>
          <dd className="mono">
            {health.database.path} · schema v{health.database.schema_version} ·{" "}
            {Math.ceil(health.database.size_bytes / 1024)} KB
          </dd>
          <dt>{locale === "zh-CN" ? "文献 / 项目" : "papers / projects"}</dt>
          <dd>
            {health.database.counts.papers ?? 0} / {health.database.counts.projects ?? 0}
          </dd>
          <dt>{locale === "zh-CN" ? "工作区" : "workspace"}</dt>
          <dd className="mono">{health.paths.workspace}</dd>
          <dt>{locale === "zh-CN" ? "日志" : "logs"}</dt>
          <dd className="mono">{health.paths.logs}</dd>
          <dt>.env</dt>
          <dd className="mono">{health.dotenv ?? (locale === "zh-CN" ? "（无）" : "(none)")}</dd>
        </dl>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="btn sm" onClick={() => void refreshHealth()}>
            {locale === "zh-CN" ? "刷新" : "Refresh"}
          </button>
          <button
            className="btn sm"
            onClick={() => void window.papercreator?.shell.openPath(health.paths.logs)}
          >
            {locale === "zh-CN" ? "打开日志目录" : "Open log folder"}
          </button>
          <button
            className="btn sm"
            onClick={() => void window.papercreator?.shell.openPath(health.paths.workspace)}
          >
            {locale === "zh-CN" ? "打开工作区" : "Open workspace"}
          </button>
        </div>
      </div>

      {usage && (
        <div className="card">
          <h3>{locale === "zh-CN" ? "模型用量（近 30 天）" : "Model usage (last 30 days)"}</h3>
          <div className="row wrap">
            <span className="chip">{totals.calls ?? 0} {locale === "zh-CN" ? "次调用" : "calls"}</span>
            <span className="chip">
              {(totals.tin ?? 0).toLocaleString()} {locale === "zh-CN" ? "输入" : "in"} / {(totals.tout ?? 0).toLocaleString()} {locale === "zh-CN" ? "输出" : "out"}
            </span>
            <span className="chip">${Number(totals.cost ?? 0).toFixed(4)}</span>
            {Number(totals.failures ?? 0) > 0 && (
              <span className="chip err">{totals.failures} {locale === "zh-CN" ? "次失败" : "failed"}</span>
            )}
          </div>
          {(usage.by_model as any[])?.length > 0 && (
            <table className="data" style={{ marginTop: 10 }}>
              <thead>
                <tr>
                  <th>{locale === "zh-CN" ? "模型" : "model"}</th>
                  <th className="num">{locale === "zh-CN" ? "调用" : "calls"}</th>
                  <th className="num">{locale === "zh-CN" ? "令牌" : "tokens"}</th>
                  <th className="num">{locale === "zh-CN" ? "成本" : "cost"}</th>
                </tr>
              </thead>
              <tbody>
                {(usage.by_model as any[]).map((row) => (
                  <tr key={`${row.provider}:${row.model}`}>
                    <td className="mono">
                      {row.provider}:{row.model}
                    </td>
                    <td className="num">{row.calls}</td>
                    <td className="num">{(row.tin + row.tout).toLocaleString()}</td>
                    <td className="num">${Number(row.cost).toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <p className="dim" style={{ fontSize: "var(--fs-xs)" }}>
            {locale === "zh-CN"
              ? "成本按你在提供方设置里填的单价估算；未填则显示为 0。"
              : "Cost is estimated from the per-million-token prices you enter per provider; zero when unset."}
          </p>
        </div>
      )}

      {cache && (
        <div className="card">
          <h3>{locale === "zh-CN" ? "缓存" : "Caches"}</h3>
          <dl className="kv">
            <dt>{locale === "zh-CN" ? "HTTP 响应" : "HTTP responses"}</dt>
            <dd>
              {(cache.http as any)?.entries ?? 0} {locale === "zh-CN" ? "项" : "entries"} ·{" "}
              {Math.ceil(((cache.http as any)?.size_bytes ?? 0) / 1024)} KB
            </dd>
            <dt>{locale === "zh-CN" ? "嵌入向量" : "embeddings"}</dt>
            <dd>
              {((cache.embeddings as any[]) ?? [])
                .map((entry) => `${entry.model}: ${entry.count}`)
                .join(", ") || (locale === "zh-CN" ? "无" : "none")}
            </dd>
          </dl>
          <div className="row" style={{ marginTop: 8 }}>
            <button
              className="btn sm"
              onClick={async () => {
                const result = await endpoints.system.maintenance({ clear_http_cache: true });
                notify({
                  kind: "success",
                  message: locale === "zh-CN" ? `已清理 ${result.http_cache_files_removed} 条缓存响应` : `Cleared ${result.http_cache_files_removed} cached responses`,
                });
                setCache(await endpoints.system.cache());
              }}
            >
              {locale === "zh-CN" ? "清空 HTTP 缓存" : "Clear HTTP cache"}
            </button>
            <button
              className="btn sm"
              onClick={async () => {
                const result = await endpoints.system.maintenance({
                  prune_jobs: true,
                  prune_prompts: true,
                  vacuum: true,
                });
                notify({
                  kind: "success",
                  message: locale === "zh-CN" ? "数据库已整理" : "Database compacted",
                  detail: locale === "zh-CN" ? `已删除 ${result.jobs_pruned ?? 0} 条旧任务和 ${result.prompts_pruned ?? 0} 条历史提示词` : `${result.jobs_pruned ?? 0} old jobs and ${result.prompts_pruned ?? 0} stored prompts removed`,
                });
                void refreshHealth();
              }}
              title={
                locale === "zh-CN"
                  ? "删除旧任务记录与历史提示词，并整理数据库；不影响文献与手稿"
                  : "Removes old job rows and stored prompts, then compacts the database. Papers and manuscripts are untouched."
              }
            >
              {locale === "zh-CN" ? "整理数据库" : "Compact database"}
            </button>
          </div>
        </div>
      )}
    </>
  );
}
