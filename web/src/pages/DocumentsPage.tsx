import { useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { ConfirmDialog, type ConfirmConfig } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { useToast } from "@/components/Toast";
import { useAuth } from "@/app/AuthContext";
import { ApiError, apiDelete, apiPatch, apiPost } from "@/lib/api";
import { canManageDocuments } from "@/lib/roles";

type RetentionResult = {
  id?: string;
  deleted?: boolean;
  deleted_at?: string | null;
  retention_hold?: boolean;
  purged?: boolean;
  purged_at?: string | null;
};

export function DocumentsPage() {
  const { role } = useAuth();
  const { push } = useToast();
  const manage = canManageDocuments(role);
  const [documentId, setDocumentId] = useState("");
  const [lastResult, setLastResult] = useState<string>("");
  const [confirm, setConfirm] = useState<ConfirmConfig | null>(null);
  const [pendingAction, setPendingAction] = useState<"soft_delete" | "purge" | null>(null);

  const softDeleteMutation = useMutation({
    mutationFn: (id: string) =>
      apiDelete<RetentionResult>(`/api/v1/documents/${encodeURIComponent(id)}`),
    onSuccess: (data) => {
      setLastResult(JSON.stringify(data, null, 2));
      push("文档已软删除");
    },
    onError: (error) =>
      push(error instanceof Error ? error.message : "软删除失败", "error"),
  });

  const holdMutation = useMutation({
    mutationFn: ({ id, retention_hold }: { id: string; retention_hold: boolean }) =>
      apiPatch<RetentionResult>(`/api/v1/documents/${encodeURIComponent(id)}`, {
        retention_hold,
      }),
    onSuccess: (data) => {
      setLastResult(JSON.stringify(data, null, 2));
      push(data.retention_hold ? "已设置保留锁定" : "已解除保留锁定");
    },
    onError: (error) =>
      push(error instanceof Error ? error.message : "更新失败", "error"),
  });

  const purgeMutation = useMutation({
    mutationFn: (id: string) =>
      apiPost<RetentionResult>(`/api/v1/documents/${encodeURIComponent(id)}/purge`, {}),
    onSuccess: (data) => {
      setLastResult(JSON.stringify(data, null, 2));
      push("文档正文与证据已销毁");
    },
    onError: (error) => {
      if (error instanceof ApiError && error.code === "PURGE_RETENTION_WINDOW") {
        push("未满最短软删保留窗，暂不可销毁", "error");
        return;
      }
      push(error instanceof Error ? error.message : "销毁失败", "error");
    },
  });

  function requireId(): string | null {
    const id = documentId.trim();
    if (!id) {
      push("请先填写 document_id", "error");
      return null;
    }
    return id;
  }

  function onLookup(event: FormEvent) {
    event.preventDefault();
    requireId();
  }

  if (!manage) {
    return <EmptyState>仅管理员可操作文档保留策略</EmptyState>;
  }

  return (
    <>
      <PageHeader
        eyebrow="Retention"
        title="文档保留"
        description="对已有 document_id 执行软删、保留锁定（retention_hold）与销毁（purge）。销毁需已软删且过保留窗。"
      />
      <form className="panel" onSubmit={onLookup} style={{ marginBottom: "0.75rem" }}>
        <div className="form-row">
          <input
            name="document_id"
            required
            value={documentId}
            onChange={(event) => setDocumentId(event.target.value)}
            placeholder="document_id"
            className="mono"
            style={{ minWidth: "18rem" }}
          />
          <button
            type="button"
            className="button ghost"
            disabled={softDeleteMutation.isPending}
            onClick={() => {
              const id = requireId();
              if (!id) return;
              setPendingAction("soft_delete");
              setConfirm({
                title: "软删除文档",
                message: `将隐藏文档 ${id} 的默认读取；可用 retention_hold 阻止后续销毁。`,
                submitLabel: "软删除",
                commentRequired: false,
                danger: true,
              });
            }}
          >
            软删除
          </button>
          <button
            type="button"
            className="button ghost"
            disabled={holdMutation.isPending}
            onClick={() => {
              const id = requireId();
              if (!id) return;
              holdMutation.mutate({ id, retention_hold: true });
            }}
          >
            锁定 hold
          </button>
          <button
            type="button"
            className="button ghost"
            disabled={holdMutation.isPending}
            onClick={() => {
              const id = requireId();
              if (!id) return;
              holdMutation.mutate({ id, retention_hold: false });
            }}
          >
            解除 hold
          </button>
          <button
            type="button"
            className="button danger"
            disabled={purgeMutation.isPending}
            onClick={() => {
              const id = requireId();
              if (!id) return;
              setPendingAction("purge");
              setConfirm({
                title: "销毁文档正文",
                message: `将清空 ${id} 正文并物理删除证据。需已软删、无 hold，且超过最短保留窗。`,
                submitLabel: "确认销毁",
                commentRequired: false,
                danger: true,
              });
            }}
          >
            销毁 purge
          </button>
        </div>
      </form>
      {lastResult ? (
        <section className="panel">
          <h3>最近操作结果</h3>
          <pre className="pre">{lastResult}</pre>
        </section>
      ) : (
        <EmptyState>输入 document_id 后选择操作</EmptyState>
      )}
      <ConfirmDialog
        open={Boolean(confirm)}
        config={confirm}
        onCancel={() => {
          setConfirm(null);
          setPendingAction(null);
        }}
        onConfirm={async () => {
          const id = requireId();
          if (!id || !pendingAction) return;
          if (pendingAction === "soft_delete") {
            await softDeleteMutation.mutateAsync(id);
          } else if (pendingAction === "purge") {
            await purgeMutation.mutateAsync(id);
          }
          setConfirm(null);
          setPendingAction(null);
        }}
      />
    </>
  );
}
