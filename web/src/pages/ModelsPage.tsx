import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PageHeader } from "@/components/PageHeader";
import { DataTable } from "@/components/DataTable";
import { StatusBadge } from "@/components/StatusBadge";
import { EmptyState, ErrorState, Skeleton } from "@/components/EmptyState";
import { useToast } from "@/components/Toast";
import { useAuth } from "@/app/AuthContext";
import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from "@/lib/api";
import { asList } from "@/lib/format";
import { canManageLlm } from "@/lib/roles";
import type { AgentConfiguration, LlmAgentBinding, LlmPreset, LlmProvider, ReviewPolicy } from "@/types/api";

const AGENT_LABELS: Record<string, string> = {
  fact_check: "Fact Checker",
  company_analysis: "Company Analyst",
  skeptic_review: "Skeptic",
  synthesize: "Synthesizer",
  default_reviewer: "Default Reviewer",
  impact_analysis: "Impact Analyst",
  plan: "Research Planner",
};

const AGENT_KEYS = Object.keys(AGENT_LABELS);

export function ModelsPage() {
  const { role } = useAuth();
  const manage = canManageLlm(role);
  const { push } = useToast();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [presetCode, setPresetCode] = useState("openai");
  const [rotateId, setRotateId] = useState<string | null>(null);
  const [rotateKey, setRotateKey] = useState("");
  const [selectedAgentKey, setSelectedAgentKey] = useState("");

  const providersQuery = useQuery({
    queryKey: ["llm-providers"],
    queryFn: () => apiGet<LlmProvider[]>("/api/v1/llm/providers"),
    enabled: manage,
  });
  const presetsQuery = useQuery({
    queryKey: ["llm-presets"],
    queryFn: () => apiGet<LlmPreset[]>("/api/v1/llm/presets"),
    enabled: manage,
  });
  const bindingsQuery = useQuery({
    queryKey: ["llm-bindings"],
    queryFn: () => apiGet<LlmAgentBinding[]>("/api/v1/llm/bindings"),
    enabled: manage,
  });
  const policyQuery = useQuery({
    queryKey: ["review-policy"],
    queryFn: () => apiGet<ReviewPolicy>("/api/v1/admin/review-policy"),
    enabled: manage,
  });
  const agentConfigsQuery = useQuery({
    queryKey: ["agent-configurations"],
    queryFn: () => apiGet<AgentConfiguration[]>("/api/v1/admin/agents"),
    enabled: manage,
  });

  const providers = asList<LlmProvider>(providersQuery.data);
  const presets = asList<LlmPreset>(presetsQuery.data);
  const bindings = asList<LlmAgentBinding>(bindingsQuery.data);
  const policy = policyQuery.data;
  const agentConfigs = asList<AgentConfiguration>(agentConfigsQuery.data);
  const selectedAgent = agentConfigs.find((item) => item.agent_key === selectedAgentKey) || agentConfigs[0];
  const bindingMap = useMemo(
    () => Object.fromEntries(bindings.map((item) => [item.agent_key, item])),
    [bindings],
  );
  const selectedPreset = presets.find((item) => item.code === presetCode) || presets[0];

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["llm-providers"] });
    await queryClient.invalidateQueries({ queryKey: ["llm-bindings"] });
    await queryClient.invalidateQueries({ queryKey: ["review-policy"] });
    await queryClient.invalidateQueries({ queryKey: ["agent-configurations"] });
  };

  const testMutation = useMutation({
    mutationFn: (id: string) =>
      apiPost<{ ok: boolean; error_code?: string; detail?: string }>(
        `/api/v1/llm/providers/${id}/test`,
        {},
      ),
    onSuccess: (data) => {
      if (data.ok) push("连通性探测成功");
      else push(data.detail || data.error_code || "探测失败", "error");
    },
    onError: (error) => push(error instanceof Error ? error.message : "探测失败", "error"),
  });

  async function onCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await apiPost("/api/v1/llm/providers", {
        code: String(data.get("code") || "").trim(),
        display_name: String(data.get("display_name") || "").trim(),
        protocol: String(data.get("protocol") || "openai_compatible"),
        base_url: String(data.get("base_url") || "").trim(),
        model: String(data.get("model") || "").trim(),
        api_key: String(data.get("api_key") || ""),
        is_default: data.get("is_default") === "on",
        timeout_seconds: Number(data.get("timeout_seconds")) || 30,
        max_tokens: Number(data.get("max_tokens")) || 2048,
        temperature: Number(data.get("temperature")) || 0.2,
      });
      push("模型接口已创建");
      setShowCreate(false);
      await invalidate();
    } catch (error) {
      push(error instanceof Error ? error.message : "创建失败", "error");
    }
  }

  async function onBind(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const providerId = String(data.get("provider_id") || "").trim();
    try {
      await apiPut("/api/v1/llm/bindings", {
        agent_key: String(data.get("agent_key")),
        provider_id: providerId || null,
        model_override: String(data.get("model_override") || "").trim() || null,
      });
      push("Agent 绑定已更新");
      await invalidate();
    } catch (error) {
      push(error instanceof Error ? error.message : "绑定失败", "error");
    }
  }

  if (!manage) {
    return <EmptyState>仅管理员可配置 LLM 接口。</EmptyState>;
  }

  return (
    <>
      <PageHeader
        eyebrow="Model Gateway"
        title="模型配置"
        description="配置 OpenAI 兼容与 Anthropic 接口，并按 Agent 绑定默认模型。密钥加密存储；memory 模式下配置会写入 .data/llm_config.json，重启后仍保留。"
        actions={
          <button type="button" className="button primary" onClick={() => setShowCreate((v) => !v)}>
            {showCreate ? "收起" : "新建接口"}
          </button>
        }
      />

      {showCreate ? (
        <form id="llm-create-form" className="panel" onSubmit={onCreate} style={{ marginBottom: "0.75rem" }}>
          <div className="form-row">
            <select
              value={presetCode}
              onChange={(event) => setPresetCode(event.target.value)}
              aria-label="供应商预设"
            >
              {presets.map((preset) => (
                <option key={preset.code} value={preset.code}>
                  {preset.display_name}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="button ghost"
              onClick={() => {
                if (!selectedPreset) return;
                const form = document.getElementById("llm-create-form") as HTMLFormElement | null;
                if (!form) return;
                (form.elements.namedItem("code") as HTMLInputElement).value = selectedPreset.code;
                (form.elements.namedItem("display_name") as HTMLInputElement).value =
                  selectedPreset.display_name;
                (form.elements.namedItem("protocol") as HTMLSelectElement).value =
                  selectedPreset.protocol;
                (form.elements.namedItem("base_url") as HTMLInputElement).value =
                  selectedPreset.base_url;
                (form.elements.namedItem("model") as HTMLInputElement).value =
                  selectedPreset.default_model || selectedPreset.models[0] || "";
              }}
            >
              应用预设
            </button>
          </div>
          <div className="form-row">
            <input name="code" required pattern="[a-z0-9_-]+" placeholder="code" defaultValue={selectedPreset?.code} />
            <input
              name="display_name"
              required
              placeholder="显示名称"
              defaultValue={selectedPreset?.display_name}
            />
            <select name="protocol" defaultValue={selectedPreset?.protocol || "openai_compatible"}>
              <option value="openai_compatible">OpenAI 兼容</option>
              <option value="anthropic">Anthropic</option>
              <option value="deterministic">Deterministic Stub</option>
            </select>
          </div>
          <div className="form-row">
            <input
              name="base_url"
              placeholder="base_url"
              style={{ minWidth: "18rem" }}
              defaultValue={selectedPreset?.base_url}
            />
            <input
              name="model"
              required
              placeholder="model"
              defaultValue={selectedPreset?.default_model}
            />
            <input name="api_key" type="password" placeholder="API Key（deterministic 可空）" />
          </div>
          <div className="form-row">
            <input name="timeout_seconds" type="number" step="1" defaultValue={30} placeholder="timeout" />
            <input name="max_tokens" type="number" defaultValue={2048} placeholder="max_tokens" />
            <input name="temperature" type="number" step="0.1" defaultValue={0.2} placeholder="temperature" />
            <label className="muted">
              <input name="is_default" type="checkbox" /> 设为默认
            </label>
            <button type="submit" className="button primary">
              保存
            </button>
          </div>
        </form>
      ) : null}

      {providersQuery.isLoading ? <Skeleton /> : null}
      {providersQuery.isError ? <ErrorState>加载模型配置失败</ErrorState> : null}
      {!providersQuery.isLoading && !providers.length ? <EmptyState>尚未配置模型接口</EmptyState> : null}

      {rotateId ? (
        <form
          className="panel"
          style={{ marginBottom: "0.75rem" }}
          onSubmit={async (event) => {
            event.preventDefault();
            try {
              await apiPost(`/api/v1/llm/providers/${rotateId}/rotate-key`, {
                api_key: rotateKey,
              });
              push("API Key 已轮换（审计已记录）");
              setRotateId(null);
              setRotateKey("");
              await invalidate();
            } catch (error) {
              push(error instanceof Error ? error.message : "轮换失败", "error");
            }
          }}
        >
          <h3>轮换 API Key</h3>
          <p className="muted">旧密钥立即失效于本地存储；审计记录操作人，不记录密钥内容。</p>
          <div className="form-row">
            <input
              type="password"
              required
              autoComplete="new-password"
              placeholder="新的 API Key"
              value={rotateKey}
              onChange={(event) => setRotateKey(event.target.value)}
            />
            <button type="submit" className="button primary">
              确认轮换
            </button>
            <button
              type="button"
              className="button ghost"
              onClick={() => {
                setRotateId(null);
                setRotateKey("");
              }}
            >
              取消
            </button>
          </div>
        </form>
      ) : null}

      <DataTable
        headers={["名称", "协议", "模型", "密钥", "状态", "默认", "操作"]}
        rows={providers.map((item) => (
          <tr key={item.id}>
            <td>
              <strong>{item.display_name}</strong>
              <div className="muted mono">{item.code}</div>
              <div className="muted" style={{ fontSize: "0.75rem" }}>
                {item.base_url || "–"}
              </div>
            </td>
            <td>
              <StatusBadge value={item.protocol} />
            </td>
            <td className="mono">{item.model}</td>
            <td className="mono">
              {item.api_key_configured || item.api_key_hint === "configured" ? "已配置" : "未配置"}
            </td>
            <td>
              <StatusBadge value={item.status} />
            </td>
            <td>{item.is_default ? "是" : "–"}</td>
            <td>
              <div className="actions">
                <button
                  type="button"
                  className="button ghost"
                  onClick={() => testMutation.mutate(item.id)}
                >
                  探测
                </button>
                {item.protocol !== "deterministic" ? (
                  <button
                    type="button"
                    className="button ghost"
                    onClick={() => {
                      setRotateId(item.id);
                      setRotateKey("");
                    }}
                  >
                    轮换密钥
                  </button>
                ) : null}
                {!item.is_default ? (
                  <button
                    type="button"
                    className="button ghost"
                    onClick={async () => {
                      try {
                        await apiPatch(`/api/v1/llm/providers/${item.id}`, { is_default: true });
                        push("已设为默认");
                        await invalidate();
                      } catch (error) {
                        push(error instanceof Error ? error.message : "失败", "error");
                      }
                    }}
                  >
                    默认
                  </button>
                ) : null}
                <button
                  type="button"
                  className="button ghost"
                  onClick={async () => {
                    const next = item.status === "active" ? "disabled" : "active";
                    try {
                      await apiPatch(`/api/v1/llm/providers/${item.id}`, { status: next });
                      push(next === "active" ? "已启用" : "已停用");
                      await invalidate();
                    } catch (error) {
                      push(error instanceof Error ? error.message : "失败", "error");
                    }
                  }}
                >
                  {item.status === "active" ? "停用" : "启用"}
                </button>
                <button
                  type="button"
                  className="button danger"
                  onClick={async () => {
                    try {
                      await apiDelete(`/api/v1/llm/providers/${item.id}`);
                      push("已删除");
                      await invalidate();
                    } catch (error) {
                      push(error instanceof Error ? error.message : "删除失败", "error");
                    }
                  }}
                >
                  删除
                </button>
              </div>
            </td>
          </tr>
        ))}
      />

      <section className="panel" style={{ marginTop: "1rem" }}>
        <h3>审核自动化</h3>
        <p className="muted">默认由 Agent 处理审核队列；低置信度、证据不足和质量门失败的任务会自动保留人工审核。</p>
        <div className="toolbar">
          <label htmlFor="review-mode">审核方式</label>
          <select
            id="review-mode"
            value={policy?.mode || "agent"}
            disabled={policyQuery.isLoading || policy?.emergency_disabled}
            onChange={async (event) => {
              try {
                await apiPatch("/api/v1/admin/review-policy", { mode: event.target.value });
                push(event.target.value === "agent" ? "已启用 Agent 自动审核" : "已切换为人工审核");
                await queryClient.invalidateQueries({ queryKey: ["review-policy"] });
              } catch (error) {
                push(error instanceof Error ? error.message : "审核策略更新失败", "error");
              }
            }}
          >
            <option value="agent">Agent 自动审核</option>
            <option value="human">人工审核</option>
          </select>
          <span className="muted">
            置信度门槛 {policy?.min_confidence ?? 0.85} · 来源 {policy?.source || "environment"}
          </span>
          {policy?.emergency_disabled ? <StatusBadge value="emergency disabled" /> : null}
        </div>
      </section>

      <section className="panel" style={{ marginTop: "1rem" }}>
        <h3>Agent 绑定</h3>
        <p className="muted">未绑定的 Agent 使用默认接口；无默认时回退本地 deterministic stub。</p>
        <form className="toolbar" onSubmit={onBind}>
          <select name="agent_key" required defaultValue="fact_check">
            {AGENT_KEYS.map((key) => (
              <option key={key} value={key}>
                {AGENT_LABELS[key]}
              </option>
            ))}
          </select>
          <select name="provider_id" defaultValue="">
            <option value="">使用默认接口</option>
            {providers.map((item) => (
              <option key={item.id} value={item.id}>
                {item.display_name} ({item.code})
              </option>
            ))}
          </select>
          <input name="model_override" placeholder="可选：覆盖 model" />
          <button type="submit" className="button primary">
            保存绑定
          </button>
          <button
            type="button"
            className="button ghost"
            onClick={async (event) => {
              const form = (event.currentTarget as HTMLButtonElement).form;
              if (!form) return;
              const data = new FormData(form);
              const providerId = String(data.get("provider_id") || "").trim();
              const modelOverride = String(data.get("model_override") || "").trim() || null;
              try {
                await apiPut("/api/v1/llm/bindings/bulk", {
                  provider_id: providerId || null,
                  model_override: modelOverride,
                });
                push(
                  providerId
                    ? `已将全部 ${AGENT_KEYS.length} 个 Agent 绑定到所选接口`
                    : `已将全部 ${AGENT_KEYS.length} 个 Agent 重置为默认接口`,
                );
                await invalidate();
              } catch (error) {
                push(error instanceof Error ? error.message : "批量绑定失败", "error");
              }
            }}
          >
            一键全部绑定
          </button>
        </form>
        <DataTable
          headers={["Agent", "当前绑定", "模型覆盖"]}
          rows={AGENT_KEYS.map((key) => {
            const binding = bindingMap[key];
            const provider = providers.find((item) => item.id === binding?.provider_id);
            return (
              <tr key={key}>
                <td>{AGENT_LABELS[key]}</td>
                <td>{provider ? `${provider.display_name} (${provider.code})` : "默认 / stub"}</td>
                <td className="mono">{binding?.model_override || "–"}</td>
              </tr>
            );
          })}
        />
        {bindingsQuery.isLoading ? <Skeleton /> : null}
      </section>

      <section className="panel" style={{ marginTop: "1rem" }}>
        <h3>Agent 配置中心</h3>
        <p className="muted">Prompt 先保存为草稿，完成校验后才可发布。运行中的 Agent 只读取已发布版本；工具权限和输出 Schema 为只读约束。</p>
        {agentConfigsQuery.isLoading ? <Skeleton /> : null}
        {agentConfigsQuery.isError ? <ErrorState>Agent 配置加载失败</ErrorState> : null}
        {selectedAgent ? <>
          <div className="form-row">
            <select value={selectedAgent.agent_key} onChange={(event) => setSelectedAgentKey(event.target.value)}>
              {agentConfigs.map((item) => <option key={item.agent_key} value={item.agent_key}>{item.display_name}</option>)}
            </select>
            <StatusBadge value={selectedAgent.enabled ? "active" : "disabled"} />
            <span className="muted">预算 {selectedAgent.budget_profile} · 已发布 {selectedAgent.published_prompt_version_id ? `v${selectedAgent.prompt_versions.find((item) => item.id === selectedAgent.published_prompt_version_id)?.number || "?"}` : "内置默认"}</span>
          </div>
          <form className="form-row" style={{ marginTop: ".75rem" }} onSubmit={async (event) => {
            event.preventDefault(); const data = new FormData(event.currentTarget);
            try { await apiPatch(`/api/v1/admin/agents/${selectedAgent.agent_key}/runtime-config`, { enabled: data.get("enabled") === "on", timeout_seconds: Number(data.get("timeout_seconds")) || null }); push("运行配置已保存"); await invalidate(); } catch (error) { push(error instanceof Error ? error.message : "保存失败", "error"); }
          }}>
            <label><input name="enabled" type="checkbox" defaultChecked={selectedAgent.enabled} /> 启用 Agent</label>
            <input name="timeout_seconds" type="number" min="1" max="120" defaultValue={selectedAgent.timeout_seconds || ""} placeholder="超时秒数（默认）" />
            <button type="submit" className="button ghost">保存运行配置</button>
          </form>
          <details style={{ marginTop: ".75rem" }}><summary>只读契约与权限</summary><p className="muted">输入：{selectedAgent.input_schema_refs.join(" · ") || "运行时上下文"}</p><p className="muted">输出：{selectedAgent.output_schema_ref}</p><p className="muted">允许工具：{selectedAgent.allowed_tools.join(" · ") || "无"}</p></details>
          <details className="agent-default-prompt" style={{ marginTop: ".75rem" }}>
            <summary>查看内置默认 Prompt（当前代码基线）</summary>
            <p className="muted">未发布自定义版本时，运行时使用此基线；平台安全约束会在调用时自动追加。</p>
            <pre className="pre">{selectedAgent.default_system_prompt}</pre>
          </details>
          <form style={{ marginTop: "1rem" }} onSubmit={async (event) => {
            event.preventDefault(); const data = new FormData(event.currentTarget);
            try { await apiPost(`/api/v1/admin/agents/${selectedAgent.agent_key}/prompt-versions`, { system_prompt: String(data.get("system_prompt") || ""), instruction_appendix: String(data.get("instruction_appendix") || ""), change_note: String(data.get("change_note") || "") }); push("Prompt 草稿已创建"); event.currentTarget.reset(); await invalidate(); } catch (error) { push(error instanceof Error ? error.message : "创建草稿失败", "error"); }
          }}>
            <h4>新建 Prompt 草稿</h4>
            <textarea name="system_prompt" required rows={6} placeholder="系统提示词：角色、分析方法、输出风格。安全与 Schema 约束由平台自动追加。" />
            <textarea name="instruction_appendix" rows={3} placeholder="业务指令附加段（可选）" style={{ marginTop: ".5rem" }} />
            <div className="form-row" style={{ marginTop: ".5rem" }}><input name="change_note" required placeholder="本次变更说明" /><button type="submit" className="button primary">保存草稿</button></div>
          </form>
          <DataTable headers={["版本", "状态", "变更说明", "验证", "操作"]} rows={selectedAgent.prompt_versions.slice().reverse().map((version) => <tr key={version.id}><td>v{version.number}</td><td><StatusBadge value={version.status} /></td><td>{version.change_note}</td><td>{version.validation?.ok ? "通过" : "未验证"}</td><td><div className="actions"><button type="button" className="button ghost" onClick={async () => { try { const result = await apiPost<{ validation: { ok: boolean } }>(`/api/v1/admin/agents/${selectedAgent.agent_key}/prompt-versions/${version.id}/validate`, {}); push(result.validation.ok ? "校验通过" : "校验未通过", result.validation.ok ? "ok" : "error"); await invalidate(); } catch (error) { push(error instanceof Error ? error.message : "校验失败", "error"); } }}>验证</button>{version.status === "validated" ? <button type="button" className="button primary" onClick={async () => { try { await apiPost(`/api/v1/admin/agents/${selectedAgent.agent_key}/prompt-versions/${version.id}/publish`, {}); push("Prompt 已发布"); await invalidate(); } catch (error) { push(error instanceof Error ? error.message : "发布失败", "error"); } }}>发布</button> : null}{version.status === "superseded" ? <button type="button" className="button ghost" onClick={async () => { try { await apiPost(`/api/v1/admin/agents/${selectedAgent.agent_key}/prompt-versions/${version.id}/rollback`, {}); push("已回滚到该版本"); await invalidate(); } catch (error) { push(error instanceof Error ? error.message : "回滚失败", "error"); } }}>回滚</button> : null}</div></td></tr>)} />
        </> : <EmptyState>暂无 Agent 配置</EmptyState>}
      </section>
    </>
  );
}
