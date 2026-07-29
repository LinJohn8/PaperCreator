/**
 * Toast notifications.
 *
 * Errors persist until dismissed and carry the backend's hint plus a button that
 * navigates to the fix - most failures here are configuration problems (no API
 * key, missing optional package, blocked model host), and a disappearing message
 * the user cannot act on is worse than none.
 */

import { useStore } from "../state/store";

export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  const dismiss = useStore((s) => s.dismissToast);
  const setView = useStore((s) => s.setView);
  const locale = useStore((s) => s.locale);

  if (!toasts.length) return null;

  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast ${toast.kind}`}>
          <div className="row" style={{ alignItems: "flex-start" }}>
            <div className="grow">
              <div className="msg">{toast.message}</div>
              {toast.detail && <div className="detail">{toast.detail}</div>}
            </div>
            <button
              className="btn icon sm"
              onClick={() => dismiss(toast.id)}
              aria-label={locale === "zh-CN" ? "关闭通知" : "Dismiss"}
            >
              ✕
            </button>
          </div>
          {toast.action && (
            <div className="actions">
              <button
                className="btn sm primary"
                onClick={() => {
                  setView(toast.action!.view);
                  dismiss(toast.id);
                }}
              >
                {toast.action.label}
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
