import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

import { ErrorBoundary } from './ErrorBoundary';

function Thrower({ shouldThrow }) {
  if (shouldThrow) {
    throw new Error('Sensitive /var/app/secret.py stack trace');
  }
  return <div data-testid="healthy-child">Healthy content</div>;
}

describe('ErrorBoundary', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('renders children when no error is thrown', () => {
    render(
      <ErrorBoundary>
        <Thrower shouldThrow={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId('healthy-child')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('catches render errors, renders a safe alert fallback without leaking error details, and logs to console', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    render(
      <ErrorBoundary>
        <Thrower shouldThrow />
      </ErrorBoundary>,
    );

    const alert = screen.getByRole('alert');
    expect(alert).toHaveClass('page-not-found');
    expect(screen.getByRole('heading', { level: 1, name: /something went wrong/i })).toBeInTheDocument();
    expect(alert.textContent).not.toContain('/var/app/secret.py');
    expect(alert.textContent).not.toContain('Sensitive');
    expect(errorSpy).toHaveBeenCalled();
  });

  it('resets error state and re-renders children when Try again is clicked', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const onReset = vi.fn();

    let fail = true;
    function RecoverableChild() {
      if (fail) {
        throw new Error('boom');
      }
      return <div data-testid="recovered-child">Recovered</div>;
    }

    render(
      <ErrorBoundary onReset={onReset}>
        <RecoverableChild />
      </ErrorBoundary>,
    );

    expect(screen.getByRole('alert')).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: /try again/i }));

    expect(onReset).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByTestId('recovered-child')).toBeInTheDocument();
  });

  it('invokes window.location.reload (or onReload) when Reload page is clicked', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const reloadSpy = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload: reloadSpy });

    render(
      <ErrorBoundary>
        <Thrower shouldThrow />
      </ErrorBoundary>,
    );

    fireEvent.click(screen.getByRole('button', { name: /reload page/i }));
    expect(reloadSpy).toHaveBeenCalledTimes(1);
  });

  it('renders custom fallback, honors custom onReload without window.location.reload, and handles Try again without onReset or re-throwing children', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const reloadSpy = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload: reloadSpy });

    // Custom fallback prop overrides default UI.
    const { unmount } = render(
      <ErrorBoundary fallback={<div data-testid="custom-fallback">Custom error UI</div>}>
        <Thrower shouldThrow />
      </ErrorBoundary>,
    );
    expect(screen.getByTestId('custom-fallback')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
    unmount();

    // Custom onReload suppresses window.location.reload(), and Try again without onReset works even if child still throws.
    const customReload = vi.fn();
    render(
      <ErrorBoundary onReload={customReload}>
        <Thrower shouldThrow />
      </ErrorBoundary>,
    );
    fireEvent.click(screen.getByRole('button', { name: /reload page/i }));
    expect(customReload).toHaveBeenCalledTimes(1);
    expect(reloadSpy).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: /try again/i }));
    expect(screen.getByRole('alert')).toBeInTheDocument();
  });
});
