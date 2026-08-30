import { Component, type ErrorInfo, type ReactNode } from "react";
import { Link } from "react-router-dom";

type Props = { children: ReactNode; brand: string };
type State = { failed: boolean };

export class SessionErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Session page crashed", error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="app">
        <div className="atmosphere" aria-hidden />
        <div className="state-card">
          <p className="brand">{this.props.brand}</p>
          <h1>This session could not be shown</h1>
          <p className="error">Something went wrong. Please try again.</p>
          <div className="actions">
            <button className="btn ghost" type="button" onClick={() => window.location.reload()}>
              Retry
            </button>
            <Link className="btn ghost" to="/">
              Home
            </Link>
          </div>
        </div>
      </div>
    );
  }
}
