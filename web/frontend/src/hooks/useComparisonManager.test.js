import { StrictMode, useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useComparisonManager } from './useComparisonManager';

function makeDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeOkResponse(payload) {
  return {
    ok: true,
    json: async () => payload,
  };
}

function useHarness(initialResults = [], initialSelected = new Set(), setStatusError = vi.fn()) {
  const [sessionResults, setSessionResults] = useState(initialResults);
  const [selectedResultIndices, setSelectedResultIndices] = useState(initialSelected);
  const manager = useComparisonManager({
    sessionResults,
    selectedResultIndices,
    setSelectedResultIndices,
    setStatusError,
  });
  return {
    ...manager,
    sessionResults,
    setSessionResults,
    selectedResultIndices,
    setSelectedResultIndices,
  };
}

describe('useComparisonManager', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('derives comparisonDbId/comparisonRefName safely and normalizes empty fields in selectAllComparable', () => {
    const results = [
      { database_id: 'db1', reference_name: '', report_json_path: 'r1' },
      { database_id: 'db1', reference_name: null, report_json_path: 'r2' },
      { database_id: 'db1', reference_name: 'NC_001', report_json_path: 'r3' },
      { database_id: 'db2', reference_name: '', report_json_path: 'r4' },
    ];
    // Include a stale out-of-range index 99 before index 0 to test firstSelected resilience.
    const { result } = renderHook(() => useHarness(results, new Set([99, 0])));

    expect(result.current.comparisonDbId).toBe('db1');
    expect(result.current.comparisonRefName).toBeNull();

    act(() => {
      result.current.selectAllComparable();
    });

    expect([...result.current.selectedResultIndices]).toEqual([0, 1]);
  });

  it('guards < 2 selected results or < 2 report_json_path artifacts', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);

    const results = [
      { database_id: 'db1', reference_name: 'REF1', report_json_path: 'r1' },
      { database_id: 'db1', reference_name: 'REF1', report_json_path: '' },
    ];
    const { result } = renderHook(() => useHarness(results, new Set([0])));

    await act(async () => {
      await result.current.fetchComparisonData();
    });
    expect(fetchSpy).not.toHaveBeenCalled();

    act(() => {
      result.current.setSelectedResultIndices(new Set([0, 1]));
    });
    await act(async () => {
      await result.current.fetchComparisonData();
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('forwards { signal }, discards stale A on out-of-order B completion, and preserves B busy state', async () => {
    const defA = makeDeferred();
    const defB = makeDeferred();
    const signals = [];
    vi.stubGlobal('fetch', vi.fn((_url, options) => {
      signals.push(options?.signal);
      return signals.length === 1 ? defA.promise : defB.promise;
    }));
    const setStatusError = vi.fn();

    const results = [
      { database_id: 'db1', reference_name: 'REF1', report_json_path: 'j1' },
      { database_id: 'db1', reference_name: 'REF1', report_json_path: 'j2' },
    ];
    const { result } = renderHook(() => useHarness(results, new Set([0, 1]), setStatusError));

    let pA;
    let pB;
    act(() => {
      pA = result.current.fetchComparisonData(false, false);
    });
    expect(result.current.isComparisonBusy).toBe(true);
    expect(signals[0].aborted).toBe(false);

    act(() => {
      pB = result.current.fetchComparisonData(true, true);
    });
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);
    expect(result.current.isComparisonBusy).toBe(true);

    // Late rejection of A (even ignoring signal) must NOT clear B's busy state or set error.
    await act(async () => {
      defA.reject(new Error('Stale A error'));
      await pA;
    });
    expect(result.current.isComparisonBusy).toBe(true);
    expect(setStatusError).not.toHaveBeenCalled();

    // B resolves cleanly.
    const payloadB = { samples: ['s1', 's2'], mutations: ['m1'], references: ['REF1'], matrix: [] };
    await act(async () => {
      defB.resolve(makeOkResponse(payloadB));
      await pB;
    });
    expect(result.current.isComparisonBusy).toBe(false);
    expect(result.current.comparisonData).toEqual(payloadB);
    expect(setStatusError).not.toHaveBeenCalled();
  });

  it('clearComparison aborts in-flight /api/compare, resets busy/selection/data, and clears targeted error', async () => {
    const deferred = makeDeferred();
    let signal;
    vi.stubGlobal('fetch', vi.fn((_url, options) => {
      signal = options?.signal;
      return deferred.promise;
    }));
    const setStatusError = vi.fn();

    const results = [
      { database_id: 'db1', reference_name: 'REF1', report_json_path: 'j1' },
      { database_id: 'db1', reference_name: 'REF1', report_json_path: 'j2' },
    ];
    const { result } = renderHook(() => useHarness(results, new Set([0, 1]), setStatusError));

    let pending;
    act(() => {
      pending = result.current.fetchComparisonData();
    });
    expect(result.current.isComparisonBusy).toBe(true);
    expect(signal.aborted).toBe(false);

    act(() => {
      result.current.clearComparison();
    });
    expect(signal.aborted).toBe(true);
    expect(result.current.isComparisonBusy).toBe(false);
    expect(result.current.comparisonData).toBeNull();
    expect(result.current.selectedResultIndices.size).toBe(0);

    // Late resolution after clearComparison is discarded.
    await act(async () => {
      deferred.resolve(makeOkResponse({ samples: ['s1'], mutations: [], references: [], matrix: [] }));
      await pending;
    });
    expect(result.current.comparisonData).toBeNull();
    expect(setStatusError).not.toHaveBeenCalled();
  });

  it('tracks targeted compare errors and aborts cleanly on unmount and under StrictMode', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockRejectedValueOnce(new Error('Compare API failed'))
      .mockResolvedValueOnce(makeOkResponse({ samples: ['s1', 's2'], mutations: [], references: [], matrix: [] })));

    const setStatusError = vi.fn();
    const results = [
      { database_id: 'db1', reference_name: 'REF1', report_json_path: 'j1' },
      { database_id: 'db1', reference_name: 'REF1', report_json_path: 'j2' },
    ];
    const { result, unmount } = renderHook(() => useHarness(results, new Set([0, 1]), setStatusError));

    await act(async () => {
      await result.current.fetchComparisonData();
    });
    expect(setStatusError).toHaveBeenCalledWith('Compare API failed');
    expect(result.current.isComparisonBusy).toBe(false);

    // Retry clears prior compare error.
    setStatusError.mockClear();
    await act(async () => {
      await result.current.fetchComparisonData();
    });
    expect(setStatusError).toHaveBeenCalledWith('');
    expect(result.current.comparisonData).not.toBeNull();

    // Unmount aborts in-flight request and suppresses AbortError.
    const pending = makeDeferred();
    let unmountSignal;
    vi.stubGlobal('fetch', vi.fn((_url, options) => {
      unmountSignal = options?.signal;
      return pending.promise;
    }));
    setStatusError.mockClear();

    let pUnmount;
    act(() => {
      pUnmount = result.current.fetchComparisonData();
    });
    unmount();
    expect(unmountSignal.aborted).toBe(true);
    await act(async () => {
      const err = new Error('Aborted');
      err.name = 'AbortError';
      pending.reject(err);
      await pUnmount;
    });
    expect(setStatusError).not.toHaveBeenCalled();

    // StrictMode compatibility
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeOkResponse({ samples: ['s1', 's2'], mutations: [], references: [], matrix: [] })));
    const { result: strictResult } = renderHook(
      () => useHarness(results, new Set([0, 1]), setStatusError),
      { wrapper: StrictMode },
    );
    await act(async () => {
      await strictResult.current.fetchComparisonData();
    });
    await waitFor(() => {
      expect(strictResult.current.comparisonData).not.toBeNull();
    });
  });

  it('sends exact /api/compare payload with state and override filter booleans, handles empty selectAllComparable, clears error in clearComparison, and discards late resolution', async () => {
    const fetchSpy = vi.fn().mockResolvedValue(
      makeOkResponse({ samples: ['s1', 's2'], mutations: [], references: ['REF1'], matrix: [] }),
    );
    vi.stubGlobal('fetch', fetchSpy);
    const setStatusError = vi.fn();

    const results = [
      { database_id: 'db1', reference_name: 'REF1', report_json_path: 'j1' },
      { database_id: 'db1', reference_name: 'REF1', report_json_path: 'j2' },
    ];
    const { result } = renderHook(() => useHarness(results, new Set(), setStatusError));

    // Empty selection -> selectAllComparable is a no-op.
    act(() => {
      result.current.selectAllComparable();
    });
    expect(result.current.selectedResultIndices.size).toBe(0);

    act(() => {
      result.current.setSelectedResultIndices(new Set([0, 1]));
      result.current.setNonSynonymousOnly(true);
    });
    await act(async () => {
      await result.current.fetchComparisonData();
    });
    expect(JSON.parse(fetchSpy.mock.calls[0][1].body)).toEqual({
      artifact_ids: ['j1', 'j2'],
      non_synonymous_only: true,
      db_hits_only: false,
    });

    await act(async () => {
      await result.current.fetchComparisonData(false, true);
    });
    expect(JSON.parse(fetchSpy.mock.calls[1][1].body)).toEqual({
      artifact_ids: ['j1', 'j2'],
      non_synonymous_only: false,
      db_hits_only: true,
    });

    // clearComparison clears prior compare error via setStatusError('').
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new Error('Boom')));
    await act(async () => {
      await result.current.fetchComparisonData();
    });
    expect(setStatusError).toHaveBeenCalledWith('Boom');
    setStatusError.mockClear();
    act(() => {
      result.current.clearComparison();
    });
    expect(setStatusError).toHaveBeenCalledWith('');

    // Out-of-order late resolution of A while B is pending discards A and keeps isComparisonBusy === true.
    const defA = makeDeferred();
    const defB = makeDeferred();
    let call = 0;
    vi.stubGlobal('fetch', vi.fn(() => (++call === 1 ? defA.promise : defB.promise)));
    act(() => {
      result.current.setSelectedResultIndices(new Set([0, 1]));
    });
    let pA;
    let pB;
    act(() => {
      pA = result.current.fetchComparisonData();
    });
    act(() => {
      pB = result.current.fetchComparisonData();
    });
    await act(async () => {
      defA.resolve(makeOkResponse({ samples: ['STALE'], mutations: [], references: [], matrix: [] }));
      await pA;
    });
    expect(result.current.isComparisonBusy).toBe(true);
    expect(result.current.comparisonData).toBeNull();

    await act(async () => {
      defB.resolve(makeOkResponse({ samples: ['FRESH'], mutations: [], references: [], matrix: [] }));
      await pB;
    });
    expect(result.current.isComparisonBusy).toBe(false);
    expect(result.current.comparisonData.samples).toEqual(['FRESH']);
  });
});
