import { useEffect, useRef, useState } from "react";

export type ConfirmConfig = {
  title: string;
  message: string;
  submitLabel?: string;
  commentRequired?: boolean;
  defaultComment?: string;
  showResume?: boolean;
  danger?: boolean;
};

export function ConfirmDialog({
  open,
  config,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  config: ConfirmConfig | null;
  onCancel: () => void;
  onConfirm: (payload: { comment: string; resumeFrom: string }) => Promise<void> | void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [comment, setComment] = useState("");
  const [resumeFrom, setResumeFrom] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open && config) {
      setComment(config.defaultComment || "");
      setResumeFrom("");
      if (!dialog.open) dialog.showModal();
    } else if (dialog.open) {
      dialog.close();
    }
  }, [open, config]);

  if (!config) {
    return <dialog ref={dialogRef} className="modal dialog" />;
  }

  return (
    <dialog
      ref={dialogRef}
      className="modal dialog"
      onCancel={(event) => {
        event.preventDefault();
        onCancel();
      }}
    >
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (config.commentRequired !== false && !comment.trim()) return;
          setBusy(true);
          try {
            await onConfirm({ comment: comment.trim(), resumeFrom });
          } finally {
            setBusy(false);
          }
        }}
      >
        <p className="eyebrow">需要确认</p>
        <h2>{config.title}</h2>
        <p className="muted">{config.message}</p>
        {config.showResume ? (
          <div className="form-field">
            <label htmlFor="resumeFrom">恢复节点</label>
            <select
              id="resumeFrom"
              value={resumeFrom}
              onChange={(event) => setResumeFrom(event.target.value)}
            >
              <option value="">由系统选择</option>
              <option value="fact_check">事实核验</option>
              <option value="company">公司分析</option>
              <option value="skeptic">反方审查</option>
              <option value="synthesize">结论合成</option>
            </select>
          </div>
        ) : null}
        <div className="form-field">
          <label htmlFor="confirmComment">操作说明</label>
          <textarea
            id="confirmComment"
            rows={4}
            maxLength={2000}
            required={config.commentRequired !== false}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />
        </div>
        <div className="action-bar">
          <button type="button" className="button ghost" onClick={onCancel} disabled={busy}>
            取消
          </button>
          <button
            type="submit"
            className={`button ${config.danger ? "danger" : "primary"}`}
            disabled={busy}
          >
            {config.submitLabel || "确认"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
