import { StrictMode } from 'react';
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Plotly from 'plotly.js-dist-min';

import { ThemeContext } from '../../hooks/useTheme';
import { usePlotlyChart } from './usePlotlyChart';

vi.mock('plotly.js-dist-min', () => ({
  default: {
    react: vi.fn(),
    purge: vi.fn(),
    Plots: {
      resize: vi.fn(),
    },
  },
}));

function TestChart({
  renderPlot,
  enabled = true,
  deps = [],
  resizeMode = 'react',
  theme = 'light',
  hideContainerOnError = false,
}) {
  return (
    <ThemeContext.Provider value={theme}>
      <TestChartInner
        renderPlot={renderPlot}
        enabled={enabled}
        deps={deps}
        resizeMode={resizeMode}
        hideContainerOnError={hideContainerOnError}
      />
    </ThemeContext.Provider>
  );
}

function TestChartInner({ renderPlot, enabled, deps, resizeMode, hideContainerOnError }) {
  const { containerRef, plotError, theme } = usePlotlyChart({
    render: renderPlot,
    enabled,
    deps,
    resizeMode,
  });

  if (hideContainerOnError && plotError) {
    return <div data-testid="error-only">{plotError}</div>;
  }

  return (
    <div data-testid="wrapper" data-theme-value={theme}>
      <div ref={containerRef} data-testid="chart-el" />
      {plotError && <p data-testid="plot-error">{plotError}</p>}
    </div>
  );
}

function installMockResizeObserver() {
  const instances = [];
  class MockResizeObserver {
    constructor(callback) {
      this.callback = callback;
      this.observed = [];
      this.disconnected = false;
      instances.push(this);
    }

    observe(target) {
      this.observed.push(target);
    }

    disconnect() {
      this.disconnected = true;
    }

    trigger(entries) {
      this.callback(entries, this);
    }
  }
  vi.stubGlobal('ResizeObserver', MockResizeObserver);
  return instances;
}

describe('usePlotlyChart', () => {
  const originalResizeObserver = globalThis.ResizeObserver;

  beforeEach(() => {
    vi.clearAllMocks();
    Plotly.Plots = { resize: vi.fn() };
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    if (originalResizeObserver === undefined) {
      delete globalThis.ResizeObserver;
    } else {
      globalThis.ResizeObserver = originalResizeObserver;
    }
  });

  it('does not purge on updates, purges when disabled, and purges active element on unmount', () => {
    const renderPlot = vi.fn();
    const { getByTestId, rerender, unmount } = render(
      <TestChart renderPlot={renderPlot} deps={[1]} />,
    );

    const el = getByTestId('chart-el');
    expect(renderPlot).toHaveBeenCalledTimes(1);
    expect(renderPlot).toHaveBeenCalledWith(el, 'light');
    expect(Plotly.purge).not.toHaveBeenCalled();

    rerender(<TestChart renderPlot={renderPlot} deps={[2]} theme="dark" />);
    expect(renderPlot).toHaveBeenCalledTimes(2);
    expect(renderPlot).toHaveBeenLastCalledWith(el, 'dark');
    expect(Plotly.purge).not.toHaveBeenCalled();

    rerender(<TestChart renderPlot={renderPlot} enabled={false} deps={[2]} theme="dark" />);
    expect(Plotly.purge).toHaveBeenCalledTimes(1);
    expect(Plotly.purge).toHaveBeenCalledWith(el);

    rerender(<TestChart renderPlot={renderPlot} enabled deps={[2]} theme="dark" />);
    expect(renderPlot).toHaveBeenCalledTimes(3);

    unmount();
    expect(Plotly.purge).toHaveBeenCalledTimes(2);
    expect(Plotly.purge).toHaveBeenLastCalledWith(el);
  });

  it('captures render errors, logs them, and recovers when container remounts', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const renderPlot = vi
      .fn()
      .mockImplementationOnce(() => {
        throw new Error('sync boom');
      })
      .mockImplementation(() => {});

    const { getByTestId, queryByTestId, rerender } = render(
      <TestChart renderPlot={renderPlot} deps={[1]} hideContainerOnError />,
    );

    expect(getByTestId('error-only')).toHaveTextContent('sync boom');
    expect(errSpy).toHaveBeenCalledTimes(1);

    rerender(<TestChart renderPlot={renderPlot} deps={[2]} hideContainerOnError />);
    expect(queryByTestId('error-only')).toBeNull();
    expect(renderPlot).toHaveBeenCalledTimes(2);
    expect(renderPlot.mock.calls[1][0]).toBe(getByTestId('chart-el'));
    errSpy.mockRestore();
  });

  it('captures rejected async render promises and avoids setting state after unmount', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    let rejectFirst;
    const renderPlot = vi.fn(() => new Promise((_, reject) => {
      rejectFirst = reject;
    }));

    const { getByTestId, rerender, unmount } = render(
      <TestChart renderPlot={renderPlot} deps={[1]} />,
    );

    await act(async () => {
      rejectFirst(new Error('async boom'));
      await Promise.resolve();
    });

    expect(getByTestId('plot-error')).toHaveTextContent('async boom');
    expect(errSpy).toHaveBeenCalledTimes(1);

    let rejectAfterUnmount;
    const renderPlot2 = vi.fn(() => new Promise((_, reject) => {
      rejectAfterUnmount = reject;
    }));
    rerender(<TestChart renderPlot={renderPlot2} deps={[2]} />);
    unmount();

    await act(async () => {
      rejectAfterUnmount(new Error('late boom'));
      await Promise.resolve();
    });

    expect(errSpy).toHaveBeenCalledTimes(2);
    errSpy.mockRestore();
  });

  it('uses ResizeObserver in react mode with Plotly.Plots.resize, ignores window resize, and skips unchanged initial sizes', () => {
    vi.useFakeTimers();
    const instances = installMockResizeObserver();
    const renderPlot = vi.fn();

    const { getByTestId } = render(
      <TestChart renderPlot={renderPlot} deps={[1]} resizeMode="react" />,
    );

    const el = getByTestId('chart-el');
    Object.defineProperty(el, 'clientWidth', { configurable: true, value: 400 });
    Object.defineProperty(el, 'clientHeight', { configurable: true, value: 250 });

    expect(instances).toHaveLength(1);
    const observer = instances[0];
    expect(observer.observed).toEqual([el]);

    // Window resize must not trigger anything.
    window.dispatchEvent(new Event('resize'));
    vi.advanceTimersByTime(400);
    expect(Plotly.Plots.resize).not.toHaveBeenCalled();

    // Re-render with positive clientWidth/clientHeight to capture initial non-zero size.
    cleanup();
    instances.length = 0;
    const renderPlot2 = vi.fn();
    const rendered = render(<TestChart renderPlot={renderPlot2} deps={[1]} resizeMode="react" />);
    const el2 = rendered.getByTestId('chart-el');
    Object.defineProperty(el2, 'clientWidth', { configurable: true, value: 400 });
    Object.defineProperty(el2, 'clientHeight', { configurable: true, value: 250 });

    // Re-attach observer by changing deps so lastWidth/lastHeight start at 400x250.
    rendered.rerender(<TestChart renderPlot={renderPlot2} deps={[2]} resizeMode="react" />);
    const obs2 = instances.at(-1);

    // Unchanged positive initial callback is skipped.
    obs2.trigger([{ contentRect: { width: 400, height: 250 } }]);
    vi.advanceTimersByTime(400);
    expect(Plotly.Plots.resize).not.toHaveBeenCalled();

    // Changed dimensions trigger Plotly.Plots.resize after 300ms debounce.
    obs2.trigger([{ contentRect: { width: 520, height: 250 } }]);
    vi.advanceTimersByTime(250);
    expect(Plotly.Plots.resize).not.toHaveBeenCalled();
    vi.advanceTimersByTime(100);
    expect(Plotly.Plots.resize).toHaveBeenCalledTimes(1);
    expect(Plotly.Plots.resize).toHaveBeenCalledWith(el2);
    expect(Plotly.react).not.toHaveBeenCalled();
  });

  it('safely skips resize when Plotly.Plots is undefined and disconnects observer on unmount', () => {
    vi.useFakeTimers();
    const instances = installMockResizeObserver();
    delete Plotly.Plots;

    const renderPlot = vi.fn();
    const { unmount } = render(
      <TestChart renderPlot={renderPlot} deps={[1]} resizeMode="react" />,
    );
    const obs = instances[0];
    obs.trigger([{ contentRect: { width: 320, height: 200 } }]);
    vi.advanceTimersByTime(350);

    unmount();
    expect(obs.disconnected).toBe(true);
  });

  it('updates resize behavior when resizeMode changes and invokes latest renderRef without re-running on inline render', () => {
    vi.useFakeTimers();
    const instances = installMockResizeObserver();
    const firstRender = vi.fn();
    const secondRender = vi.fn();

    const { getByTestId, rerender } = render(
      <TestChart renderPlot={firstRender} deps={[1]} resizeMode="react" />,
    );
    expect(firstRender).toHaveBeenCalledTimes(1);

    // Inline render identity change alone must not trigger re-render.
    rerender(<TestChart renderPlot={secondRender} deps={[1]} resizeMode="react" />);
    expect(firstRender).toHaveBeenCalledTimes(1);
    expect(secondRender).not.toHaveBeenCalled();

    // Changing resizeMode re-subscribes ResizeObserver to use 'rerender' mode.
    rerender(<TestChart renderPlot={secondRender} deps={[1]} resizeMode="rerender" />);
    expect(instances).toHaveLength(2);
    expect(instances[0].disconnected).toBe(true);

    instances[1].trigger([{ contentRect: { width: 640, height: 320 } }]);
    vi.advanceTimersByTime(350);

    expect(secondRender).toHaveBeenCalledTimes(1);
    expect(secondRender).toHaveBeenCalledWith(getByTestId('chart-el'), 'light');
    expect(Plotly.Plots.resize).not.toHaveBeenCalled();
  });

  it('surfaces resize errors via console.error and plotError instead of swallowing them', () => {
    vi.useFakeTimers();
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const instances = installMockResizeObserver();
    Plotly.Plots.resize = vi.fn(() => {
      throw new Error('resize boom');
    });

    const { getByTestId } = render(
      <TestChart renderPlot={vi.fn()} deps={[1]} resizeMode="react" />,
    );

    act(() => {
      instances[0].trigger([{ contentRect: { width: 480, height: 240 } }]);
      vi.advanceTimersByTime(350);
    });

    expect(getByTestId('plot-error')).toHaveTextContent('resize boom');
    expect(errSpy).toHaveBeenCalledTimes(1);
    errSpy.mockRestore();
  });

  it('handles missing ResizeObserver gracefully', () => {
    delete globalThis.ResizeObserver;
    const renderPlot = vi.fn();
    render(<TestChart renderPlot={renderPlot} deps={[1]} />);
    expect(renderPlot).toHaveBeenCalledTimes(1);
  });

  it('surfaces synchronous render errors under React.StrictMode instead of suppressing them', () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const renderPlot = vi.fn(() => {
      throw new Error('strict sync boom');
    });

    const { getByTestId } = render(
      <StrictMode>
        <TestChart renderPlot={renderPlot} deps={[1]} />
      </StrictMode>,
    );

    expect(getByTestId('plot-error')).toHaveTextContent('strict sync boom');
    expect(errSpy).toHaveBeenCalled();
    errSpy.mockRestore();
  });

  it('ignores stale async render rejections after deps advance to a newer successful render', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    let rejectStale;
    const renderPlot = vi
      .fn()
      .mockImplementationOnce(() => new Promise((_, reject) => {
        rejectStale = reject;
      }))
      .mockImplementationOnce(() => Promise.resolve());

    const { queryByTestId, rerender } = render(
      <TestChart renderPlot={renderPlot} deps={[1]} />,
    );

    await act(async () => {
      rerender(<TestChart renderPlot={renderPlot} deps={[2]} />);
      await Promise.resolve();
    });

    expect(queryByTestId('plot-error')).toBeNull();

    await act(async () => {
      rejectStale(new Error('stale dep 1 boom'));
      await Promise.resolve();
    });

    expect(queryByTestId('plot-error')).toBeNull();
    expect(errSpy).toHaveBeenCalledTimes(1);
    errSpy.mockRestore();
  });

  it('skips ResizeObserver callbacks with 0x0 contentRect while resizing on positive contentRect', () => {
    vi.useFakeTimers();
    const instances = installMockResizeObserver();
    const renderPlot = vi.fn();

    const { getByTestId } = render(
      <TestChart renderPlot={renderPlot} deps={[1]} resizeMode="react" />,
    );
    const el = getByTestId('chart-el');
    const obs = instances[0];

    obs.trigger([{ contentRect: { width: 0, height: 0 } }]);
    vi.advanceTimersByTime(400);
    expect(Plotly.Plots.resize).not.toHaveBeenCalled();

    obs.trigger([{ contentRect: { width: 400, height: 0 } }]);
    vi.advanceTimersByTime(400);
    expect(Plotly.Plots.resize).not.toHaveBeenCalled();

    obs.trigger([{ contentRect: { width: 640, height: 480 } }]);
    vi.advanceTimersByTime(350);
    expect(Plotly.Plots.resize).toHaveBeenCalledTimes(1);
    expect(Plotly.Plots.resize).toHaveBeenCalledWith(el);
  });
});
