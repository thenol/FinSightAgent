import { Component, type ErrorInfo, type ReactNode } from "react";

type Props = {
  children: ReactNode;
};

type State = {
  error: Error | null;
};

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("FinSight admin render error", error, info.componentStack);
  }

  private reset = () => {
    this.setState({ error: null });
  };

  render() {
    if (this.state.error) {
      return (
        <main className="panel error-boundary" role="alert">
          <h1>页面渲染失败</h1>
          <p className="muted">请刷新页面或返回总览。若问题持续，请联系运维并提供浏览器控制台日志。</p>
          <pre>{this.state.error.message}</pre>
          <div className="button-row">
            <button type="button" className="button" onClick={this.reset}>
              重试
            </button>
            <a className="button ghost" href="/admin/">
              返回总览
            </a>
          </div>
        </main>
      );
    }
    return this.props.children;
  }
}
