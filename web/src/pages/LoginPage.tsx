import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "@/app/AuthContext";
import { ApiError } from "@/lib/api";

export function LoginPage() {
  const { token, login } = useAuth();
  const location = useLocation();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (token) {
    const redirect = (location.state as { from?: string } | null)?.from || "/";
    return <Navigate to={redirect} replace />;
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(username, password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="login-shell" aria-labelledby="loginTitle">
      <form className="login-card" onSubmit={onSubmit}>
        <div className="brand-mark" aria-hidden="true">
          FS
        </div>
        <p className="eyebrow">FinSight Admin</p>
        <h1 id="loginTitle">登录管理后台</h1>
        <p className="muted">审核事件证据、研究报告与工作流状态。</p>
        <div className="form-field">
          <label htmlFor="username">用户名</label>
          <input
            id="username"
            name="username"
            autoComplete="username"
            required
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </div>
        <div className="form-field">
          <label htmlFor="password">密码</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        <button className="button primary" type="submit" disabled={busy} style={{ width: "100%" }}>
          {busy ? "登录中…" : "登录"}
        </button>
        {error ? (
          <div className="error-state" role="alert" style={{ marginTop: "0.75rem" }}>
            {error}
          </div>
        ) : null}
      </form>
    </section>
  );
}
