/**
 * Output panel: backend process output, server log, and the job queue.
 *
 * The backend runs as a child process, so its stdout is the primary place a
 * failure shows up. Surfacing it in-app rather than requiring a terminal is the
 * difference between a diagnosable problem and an inexplicable one.
 */

import { useEffect, useRef, useState } from "react";

import * as endpoints from "../api/endpoints";
import { useStore } from "../state/store";

type Tab = "process" | "log" | "errors" | "jobs";

export function OutputPanel({ standalone = false }: { standalone?: boolean }) {
  const [tab, setTab] = useState<Tab>("process");
  const backendLog = useStore((s) => s.backendLog);
  const jobs = useStore((s) => s.jobs);
  const loadJobs = useStore((s) => s.loadJobs);
  const togglePanel = useStore((s) => s.togglePanel);
  const appendBackendLog = useStore((s) => s.appendBackendLog);
  const [serverLog, setServerLog] = useState<string[]>([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const locale = useStore((s) => s.locale);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Seed from the main process's ring buffer, so the panel is not empty when it
  // is opened after startup.
  useEffect(() => {
    if (backendLog.length) return;
    void window.papercreator?.backend.log().then((lines) => {
      lines.forEach((line) => appendBackendLog(line));
    });
  }, [backendLog.length, appendBackendLog]);

  useEffect(() => {
    if (tab !== "log" && tab !== "errors") return;
    let cancelled = false;
    const fetchLog = () =>
      endpoints.system
        .logs(tab === "errors" ? "errors" : "main", 400)
        .then((result) => {
          if (!cancelled) setServerLog(result.lines);
        })
        .catch(() => {
          if (!cancelled) setServerLog([locale === "zh-CN" ? "（无法读取日志文件）" : "(the log file could not be read)"]);
        });
    void fetchLog();
    const timer = setInterval(fetchLog, 4000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [tab, locale]);

  useEffect(() => {
    if (tab === "jobs") void loadJobs();
  }, [tab, loadJobs]);

  useEffect(() => {
    if (autoScroll && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  });

  const lines =
    tab === "process"
      ? backendLog.map(
          (line) => `${line.stream === "stderr" ? "! " : "  "}${line.text}`,
        )
      : serverLog;

  return (
    <div className="panel" style={standalone ? { height: "100%", borderTop: "none" } : undefined}>
      <div className="panel-tabs">
        {([
          ["process", locale === "zh-CN" ? "后端进程" : "Backend process"],
          ["log", locale === "zh-CN" ? "服务日志" : "Server log"],
          ["errors", locale === "zh-CN" ? "错误" : "Errors"],
          ["jobs", locale === "zh-CN" ? "任务" : "Jobs"],
        ] as [Tab, string][]).map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? "active" : ""}
            onClick={() => setTab(id)}
          >
            {label}
            {id === "jobs" && jobs.some((job) => job.status === "running") ? " ●" : ""}
          </button>
        ))}
        <div className="grow" />
        <label className="row" style={{ gap: 4, fontSize: "var(--fs-xs)" }}>
          <input
            type="checkbox"
            checked={autoScroll}
            onChange={(event) => setAutoScroll(event.target.checked)}
          />
          {locale === "zh-CN" ? "跟随" : "follow"}
        </label>
        <button
          className="btn icon sm"
          onClick={() => void window.papercreator?.backend.restart()}
          title={locale === "zh-CN" ? "重启后端进程" : "Restart the backend process"}
        >
          ⟳
        </button>
        {!standalone && (
          <button className="btn icon sm" onClick={() => togglePanel(false)} title={locale === "zh-CN" ? "关闭" : "Close"}>
            ✕
          </button>
        )}
      </div>

      <div className="panel-body" ref={bodyRef}>
        {tab === "jobs" ? (
          <JobList />
        ) : lines.length ? (
          lines.join("\n")
        ) : (
          <span className="dim">
            {tab === "process"
              ? locale === "zh-CN" ? "尚未捕获后端输出；开发模式下，后端可能运行在独立终端中。" : "No backend output captured. In development the backend may be running in a separate terminal."
              : locale === "zh-CN" ? "日志为空。" : "The log is empty."}
          </span>
        )}
      </div>
    </div>
  );
}

function JobList() {
  const jobs = useStore((s) => s.jobs);
  const notify = useStore((s) => s.notify);
  const loadJobs = useStore((s) => s.loadJobs);
  const locale = useStore((s) => s.locale);

  async function cancel(jobId: string) {
    try {
      await endpoints.system.cancelJob(jobId);
      notify({
        kind: "info",
        message: locale === "zh-CN" ? "已请求取消" : "Cancellation requested",
        detail: locale === "zh-CN" ? "任务将在下一个检查点停止。" : "The job stops at its next checkpoint.",
      });
      await loadJobs();
    } catch (error) {
      useStore.getState().reportError(error, locale === "zh-CN" ? "取消任务" : "cancelling the job");
    }
  }

  if (!jobs.length) return <span className="dim">{locale === "zh-CN" ? "暂无任务。" : "No jobs yet."}</span>;

  return (
    <table className="data" style={{ fontFamily: "var(--font)" }}>
      <thead>
        <tr>
          <th>{locale === "zh-CN" ? "类型" : "Kind"}</th>
          <th>{locale === "zh-CN" ? "状态" : "Status"}</th>
          <th style={{ width: 160 }}>{locale === "zh-CN" ? "进度" : "Progress"}</th>
          <th>{locale === "zh-CN" ? "消息" : "Message"}</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => (
          <tr key={job.id}>
            <td className="mono">{job.kind}</td>
            <td>
              <span
                className={`chip ${
                  job.status === "done"
                    ? "ok"
                    : job.status === "failed"
                      ? "err"
                      : job.status === "running"
                        ? "on"
                        : ""
                }`}
              >
                {locale === "zh-CN" ? ({ queued: "排队中", running: "运行中", done: "已完成", failed: "失败", cancelled: "已取消" } as Record<string, string>)[job.status] || job.status : job.status}
              </span>
            </td>
            <td>
              <div className="progress">
                <div style={{ width: `${Math.round((job.progress || 0) * 100)}%` }} />
              </div>
            </td>
            <td className="truncate" title={job.error || job.message}>
              {job.error ? <span className="err-text">{job.error}</span> : job.message}
            </td>
            <td>
              {(job.status === "running" || job.status === "queued") && (
                <button className="btn sm" onClick={() => void cancel(job.id)}>
                  {locale === "zh-CN" ? "取消" : "cancel"}
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
