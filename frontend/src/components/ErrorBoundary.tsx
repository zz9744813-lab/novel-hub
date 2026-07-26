import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  label?: string;
}

interface State {
  error: Error | null;
}

/** Prevent a single panel crash from wiping the whole cockpit to a black screen. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: unknown) {
    console.error("[ErrorBoundary]", this.props.label || "panel", error, info);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="panel-elevated rounded-lg p-6 max-w-lg mx-auto mt-10 space-y-3">
          <h2 className="text-sm text-text-primary" style={{ fontWeight: 510 }}>
            界面渲染出错{this.props.label ? ` · ${this.props.label}` : ""}
          </h2>
          <p className="text-xs text-red-400 font-mono break-all">
            {this.state.error.message || String(this.state.error)}
          </p>
          <button
            className="btn-primary px-3 py-1.5 text-xs rounded-md"
            onClick={() => this.setState({ error: null })}
          >
            重试渲染
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
