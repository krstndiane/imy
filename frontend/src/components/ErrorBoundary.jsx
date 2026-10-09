import { Component } from "react";

/* ErrorBoundary: if something inside breaks while drawing the screen, show a small "try again" card
   instead of a blank page. The rest of the app (sidebar, other people) keeps working.
   `resetKey`: when it changes (e.g. you open another person), the boundary tries again by itself. */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("[imy] screen error:", error, info?.componentStack);
  }

  componentDidUpdate(prev) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="crash" role="alert">
        <h2>Something went wrong on this screen</h2>
        <p className="muted">Your chats and everything imy learned are safe.</p>
        <div className="actions">
          <button className="btn primary" onClick={() => this.setState({ error: null })}>Try again</button>
          <button className="btn" onClick={() => window.location.reload()}>Reload the page</button>
        </div>
      </div>
    );
  }
}
