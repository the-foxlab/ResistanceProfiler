import React from 'react';

/**
 * Root error boundary catching unexpected React render/lifecycle errors
 * without exposing raw stack traces or internal paths in the DOM.
 */
export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false };
    this.handleReset = this.handleReset.bind(this);
    this.handleReload = this.handleReload.bind(this);
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  componentDidCatch(error, errorInfo) {
    console.error('Unhandled application error:', error, errorInfo);
  }

  handleReset() {
    this.setState({ hasError: false });
    if (typeof this.props.onReset === 'function') {
      this.props.onReset();
    }
  }

  handleReload() {
    if (typeof this.props.onReload === 'function') {
      this.props.onReload();
      return;
    }
    window.location.reload();
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }
      return (
        <div className="page-not-found" role="alert">
          <h1>Something went wrong</h1>
          <p>
            An unexpected error occurred while rendering this page. You can try again or reload the page.
          </p>
          <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
            <button type="button" onClick={this.handleReset}>
              Try again
            </button>
            <button type="button" onClick={this.handleReload}>
              Reload page
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}
