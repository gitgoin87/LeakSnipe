import { Component, type ErrorInfo, type ReactNode } from "react";

type ErrorBoundaryProps = {
  children: ReactNode;
};

type ErrorBoundaryState = {
  error: Error | null;
};

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[LeakSnipe UI]", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="app-shell">
          <header className="app-header">
            <div className="brand">
              <span className="brand-kicker">LeakSnipe</span>
              <span className="brand-title">Poker Therapist</span>
            </div>
          </header>
          <main className="content" style={{ padding: "1.5rem" }}>
            <h1 className="panel-title">UI failed to load</h1>
            <div className="error-banner" role="alert">
              {this.state.error.message || "Unknown React error"}
            </div>
            <p className="panel-subtitle">
              Try Refresh below. If the sidecar is offline, run{" "}
              <code className="mono">Start-Sidecar.bat</code> or restart via{" "}
              <code className="mono">Launch-LeakSnipe.bat</code>. Log:{" "}
              <code className="mono">%TEMP%\leaksnipe_sidecar.log</code>
            </p>
            <button
              type="button"
              className="primary-btn"
              onClick={() => {
                this.setState({ error: null });
                window.location.reload();
              }}
            >
              Reload LeakSnipe
            </button>
          </main>
        </div>
      );
    }

    return this.props.children;
  }
}
