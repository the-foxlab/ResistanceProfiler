import { StrictMode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useMutationBrowser } from './useMutationBrowser';

function makeDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makePayload(items = [], formulaItems = [], plotMeta = { references: [], features: [] }) {
  return {
    ok: true,
    json: async () => ({
      data: {
        items,
        formula_items: formulaItems,
        plot_meta: plotMeta,
      },
    }),
  };
}

describe('useMutationBrowser', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('starts with empty defaults and does not fetch when loadMutations receives an empty databaseId initially', async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    const setStatusError = vi.fn();

    const { result } = renderHook(() => useMutationBrowser({ setStatusError }));

    expect(result.current.rules).toEqual([]);
    expect(result.current.formulaRules).toEqual([]);
    expect(result.current.mutationPlotMeta).toEqual({ references: [], features: [] });
    expect(result.current.mutationsLoaded).toBe(false);
    expect(result.current.isMutationsLoading).toBe(false);
    expect(result.current.loadedDatabaseId).toBeNull();
    expect(result.current.mutationColumns).toEqual([]);
    expect(result.current.formulaColumns).toEqual([]);

    await act(async () => {
      await result.current.loadMutations('');
      await result.current.loadMutations(null);
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(setStatusError).not.toHaveBeenCalled();
    expect(result.current.mutationsLoaded).toBe(false);
    expect(result.current.isMutationsLoading).toBe(false);
  });

  it('loads, formats, sorts, filters, and exports mutations while gating TSV export until loaded', async () => {
    const createObjectURLSpy = vi.fn(() => 'blob:mock-tsv');
    const revokeObjectURLSpy = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: createObjectURLSpy,
      revokeObjectURL: revokeObjectURLSpy,
    });

    const deferred = makeDeferred();
    const fetchSpy = vi.fn(() => deferred.promise);
    vi.stubGlobal('fetch', fetchSpy);
    const setStatusError = vi.fn();

    const { result } = renderHook(() => useMutationBrowser({ setStatusError }));

    // Guarded while !mutationsLoaded
    act(() => {
      result.current.downloadMutationsAsTsv();
      result.current.downloadFormulaRulesAsTsv();
    });
    expect(createObjectURLSpy).not.toHaveBeenCalled();

    let loadPromise;
    act(() => {
      loadPromise = result.current.loadMutations('db1');
    });

    expect(result.current.isMutationsLoading).toBe(true);
    expect(result.current.mutationsLoaded).toBe(false);
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/mutations?database_id=db1',
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    await act(async () => {
      deferred.resolve(makePayload(
        [
          { id: 1, feature: 'UL23', position: 9, mutation: 'M10V', drug: 'ACV', comment: '' },
          { id: 2, feature: 'UL30', position: 1, mutation: 'A2T', drug: 'FOS', comment: 'Note' },
        ],
        [
          { formula_id: 10, label: 'ComboA', drug: 'ACV' },
          { formula_id: 11, label: 'ComboB', drug: 'GCV' },
        ],
        {
          references: [],
          features: [],
          drug_aliases: { ACV: 'Acyclovir' },
        },
      ));
      await loadPromise;
    });

    expect(result.current.isMutationsLoading).toBe(false);
    expect(result.current.mutationsLoaded).toBe(true);
    expect(result.current.loadedDatabaseId).toBe('db1');
    expect(result.current.rules).toHaveLength(2);
    expect(result.current.formulaRules).toHaveLength(2);

    const posCol = result.current.mutationColumns.find((col) => col.key === 'position');
    expect(posCol.accessor(result.current.rules[0])).toBe('10'); // 0-based 9 -> 1-based 10

    const drugColIndex = result.current.mutationColumns.findIndex((col) => col.key === 'drug');
    const drugCol = result.current.mutationColumns[drugColIndex];
    expect(drugCol.accessor(result.current.rules[0])).toBe('ACV (Acyclovir)');
    expect(drugCol.sortAccessor(result.current.rules[0])).toBe('ACV');

    // Sort by position numerically
    const posColIndex = result.current.mutationColumns.findIndex((col) => col.key === 'position');
    act(() => {
      result.current.setMutationSortColumn(posColIndex);
      result.current.setMutationSortAsc(true);
    });
    expect(result.current.displayedRules.map((r) => r.id)).toEqual([2, 1]);

    // Filter single and formula rules
    act(() => {
      result.current.setMutationFilter('ul30');
      result.current.setFormulaFilter('combob');
    });
    expect(result.current.displayedRules.map((r) => r.id)).toEqual([2]);
    expect(result.current.displayedFormulaRules.map((r) => r.formula_id)).toEqual([11]);

    // Exports work once loaded
    act(() => {
      result.current.downloadMutationsAsTsv();
      result.current.downloadFormulaRulesAsTsv();
    });
    expect(createObjectURLSpy).toHaveBeenCalledTimes(2);
  });

  it('clears stale state synchronously on DB switch, resets filters/sort, and aborts the prior request', async () => {
    const first = makeDeferred();
    const second = makeDeferred();
    const signals = [];
    const fetchSpy = vi.fn((_, options) => {
      signals.push(options?.signal);
      return signals.length === 1 ? first.promise : second.promise;
    });
    vi.stubGlobal('fetch', fetchSpy);
    const setStatusError = vi.fn();

    const { result } = renderHook(() => useMutationBrowser({ setStatusError }));

    await act(async () => {
      const p1 = result.current.loadMutations('dbA');
      first.resolve(makePayload(
        [{ id: 1, feature: 'UL23', position: 0, mutation: 'M1V', drug: 'ACV' }],
        [{ formula_id: 10, label: 'ComboA', drug: 'ACV' }],
      ));
      await p1;
    });

    act(() => {
      result.current.setMutationFilter('UL23');
      result.current.setFormulaFilter('ComboA');
      result.current.setMutationSortColumn(1);
      result.current.setMutationSortAsc(false);
    });

    // Switch to dbB: stale data/filters/sort clear synchronously before dbB resolves.
    let p2;
    act(() => {
      p2 = result.current.loadMutations('dbB');
    });

    expect(result.current.rules).toEqual([]);
    expect(result.current.formulaRules).toEqual([]);
    expect(result.current.mutationPlotMeta).toEqual({ references: [], features: [] });
    expect(result.current.mutationsLoaded).toBe(false);
    expect(result.current.loadedDatabaseId).toBeNull();
    expect(result.current.isMutationsLoading).toBe(true);
    expect(result.current.mutationFilter).toBe('');
    expect(result.current.formulaFilter).toBe('');
    expect(result.current.mutationSortColumn).toBeNull();
    expect(result.current.mutationSortAsc).toBe(true);
    expect(setStatusError).not.toHaveBeenCalled();

    // Switch to dbC while dbB is in flight -> dbB's AbortSignal must be aborted.
    const third = makeDeferred();
    fetchSpy.mockImplementationOnce((_, options) => {
      signals.push(options?.signal);
      return third.promise;
    });
    let p3;
    act(() => {
      p3 = result.current.loadMutations('dbC');
    });
    expect(signals[1].aborted).toBe(true);
    expect(signals[2].aborted).toBe(false);

    // Even if dbB resolves or rejects late ignoring signal, dbC wins.
    await act(async () => {
      second.resolve(makePayload([{ id: 2, feature: 'STALE_B' }]));
      await p2;
    });
    expect(result.current.rules).toEqual([]);
    expect(result.current.loadedDatabaseId).toBeNull();

    await act(async () => {
      third.resolve(makePayload([{ id: 3, feature: 'FRESH_C' }]));
      await p3;
    });
    expect(result.current.rules).toEqual([{ id: 3, feature: 'FRESH_C' }]);
    expect(result.current.loadedDatabaseId).toBe('dbC');
    expect(result.current.mutationsLoaded).toBe(true);
    expect(result.current.isMutationsLoading).toBe(false);
    expect(setStatusError).not.toHaveBeenCalled();

    // Switching back to empty clears loaded state without fetching.
    await act(async () => {
      await result.current.loadMutations('');
    });
    expect(fetchSpy).toHaveBeenCalledTimes(3);
    expect(result.current.rules).toEqual([]);
    expect(result.current.mutationsLoaded).toBe(false);
    expect(result.current.loadedDatabaseId).toBeNull();
  });

  it('drops stale request completions and errors on reversed A/B responses', async () => {
    const defA = makeDeferred();
    const defB = makeDeferred();
    let call = 0;
    vi.stubGlobal('fetch', vi.fn(() => {
      call += 1;
      return call === 1 ? defA.promise : defB.promise;
    }));
    const setStatusError = vi.fn();

    const { result } = renderHook(() => useMutationBrowser({ setStatusError }));

    let pA;
    let pB;
    act(() => {
      pA = result.current.loadMutations('dbA');
      pB = result.current.loadMutations('dbB');
    });

    await act(async () => {
      defB.resolve(makePayload([{ id: 2, feature: 'DB_B' }]));
      await pB;
    });
    expect(result.current.loadedDatabaseId).toBe('dbB');
    expect(result.current.rules).toEqual([{ id: 2, feature: 'DB_B' }]);

    // Late rejection from A must not clobber B or trigger statusError.
    await act(async () => {
      defA.reject(new Error('Late A failure'));
      await pA;
    });
    expect(result.current.loadedDatabaseId).toBe('dbB');
    expect(result.current.rules).toEqual([{ id: 2, feature: 'DB_B' }]);
    expect(result.current.mutationsLoaded).toBe(true);
    expect(setStatusError).not.toHaveBeenCalled();
  });

  it('clears stale DB A data on DB B failure and clears only mutation errors on subsequent load', async () => {
    vi.stubGlobal('fetch', vi.fn()
      .mockResolvedValueOnce(makePayload([{ id: 1, feature: 'DB_A' }], [{ formula_id: 9, label: 'F_A' }]))
      .mockRejectedValueOnce(new Error('Server offline'))
      .mockResolvedValueOnce(makePayload([{ id: 1, feature: 'DB_A' }]))
      .mockResolvedValueOnce(makePayload([{ id: 3, feature: 'DB_C' }])));

    const setStatusError = vi.fn();
    const { result } = renderHook(() => useMutationBrowser({ setStatusError }));

    await act(async () => {
      await result.current.loadMutations('dbA');
    });
    expect(result.current.mutationsLoaded).toBe(true);
    expect(result.current.loadedDatabaseId).toBe('dbA');

    await act(async () => {
      await result.current.loadMutations('dbB');
    });
    expect(result.current.rules).toEqual([]);
    expect(result.current.formulaRules).toEqual([]);
    expect(result.current.mutationPlotMeta).toEqual({ references: [], features: [] });
    expect(result.current.mutationsLoaded).toBe(false);
    expect(result.current.loadedDatabaseId).toBeNull();
    expect(result.current.isMutationsLoading).toBe(false);
    expect(setStatusError).toHaveBeenCalledWith('Error loading mutations: Server offline');

    // Subsequent load clears the prior mutation error.
    setStatusError.mockClear();
    await act(async () => {
      await result.current.loadMutations('dbA');
    });
    expect(setStatusError).toHaveBeenCalledWith('');
    expect(result.current.loadedDatabaseId).toBe('dbA');

    // Switching when no mutation error occurred does NOT clear unrelated statusError.
    setStatusError.mockClear();
    await act(async () => {
      await result.current.loadMutations('dbC');
    });
    expect(setStatusError).not.toHaveBeenCalled();
  });

  it('aborts in-flight requests on external signal abort and unmount (including StrictMode)', async () => {
    const pending = makeDeferred();
    const observedSignals = [];
    vi.stubGlobal('fetch', vi.fn((_, options) => {
      observedSignals.push(options?.signal);
      return pending.promise;
    }));
    const setStatusError = vi.fn();

    // External signal abort
    const externalController = new AbortController();
    const { result, unmount } = renderHook(() => useMutationBrowser({ setStatusError }));

    let p;
    act(() => {
      p = result.current.loadMutations('db1', { signal: externalController.signal });
    });
    expect(observedSignals[0].aborted).toBe(false);

    act(() => {
      externalController.abort();
    });
    expect(observedSignals[0].aborted).toBe(true);

    await act(async () => {
      const abortErr = new Error('The operation was aborted.');
      abortErr.name = 'AbortError';
      pending.reject(abortErr);
      await p;
    });
    expect(setStatusError).not.toHaveBeenCalled();
    expect(result.current.isMutationsLoading).toBe(false);

    // Unmount while request is in flight
    const late = makeDeferred();
    vi.stubGlobal('fetch', vi.fn((_, options) => {
      observedSignals.push(options?.signal);
      return late.promise;
    }));
    act(() => {
      result.current.loadMutations('db2');
    });
    expect(observedSignals[1].aborted).toBe(false);
    unmount();
    expect(observedSignals[1].aborted).toBe(true);

    await act(async () => {
      late.resolve(makePayload([{ id: 99, feature: 'GHOST' }]));
      await Promise.resolve();
    });
    expect(setStatusError).not.toHaveBeenCalled();

    // StrictMode mount/unmount/remount
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makePayload([{ id: 4, feature: 'STRICT' }])));
    const { result: strictResult } = renderHook(() => useMutationBrowser({ setStatusError }), {
      wrapper: StrictMode,
    });
    await act(async () => {
      await strictResult.current.loadMutations('dbStrict');
    });
    await waitFor(() => {
      expect(strictResult.current.loadedDatabaseId).toBe('dbStrict');
      expect(strictResult.current.rules).toEqual([{ id: 4, feature: 'STRICT' }]);
    });
  });

  it('skips fetch on pre-aborted external signal, preserves filter/sort on same-DB reload, and handles edge sorting & column formatting', async () => {
    const fetchSpy = vi.fn()
      .mockResolvedValueOnce(makePayload(
        [
          { id: 1, reference_name: 'REF1', position: 10, drug: 'Beta', score: 0, extra_col: null },
          { id: 2, reference_name: 'REF1', position: 2, drug: 'alpha', score: 15, extra_col: 'tag' },
          { id: 3, reference_name: 'REF1', position: null, drug: '', score: '', extra_col: undefined },
        ],
        [],
        { references: [], features: [] },
      ))
      .mockResolvedValueOnce({ ok: true, json: async () => ({}) });
    vi.stubGlobal('fetch', fetchSpy);
    const setStatusError = vi.fn();

    const { result } = renderHook(() => useMutationBrowser({ setStatusError }));

    // Pre-aborted external signal skips fetch and keeps isMutationsLoading === false.
    const preAborted = new AbortController();
    preAborted.abort();
    await act(async () => {
      await result.current.loadMutations('db1', { signal: preAborted.signal });
    });
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(result.current.isMutationsLoading).toBe(false);

    // Load db1 with rules containing unknown key `extra_col`, `0`, `null`, `undefined`, and mixed numeric/text.
    await act(async () => {
      await result.current.loadMutations('db1');
    });

    const colKeys = result.current.mutationColumns.map((c) => c.key);
    expect(colKeys.at(-1)).toBe('extra_col');

    const scoreIdx = colKeys.indexOf('score');
    const drugIdx = colKeys.indexOf('drug');
    const extraIdx = colKeys.indexOf('extra_col');

    // Numeric formatting: 0 is preserved, null/undefined become ''.
    expect(result.current.mutationColumns[scoreIdx].accessor(result.current.rules[0])).toBe('0');
    expect(result.current.mutationColumns[extraIdx].accessor(result.current.rules[0])).toBe('');
    expect(result.current.mutationColumns[extraIdx].accessor(result.current.rules[2])).toBe('');

    // Descending numeric sort + case-insensitive text sort + out-of-range sort column.
    act(() => {
      result.current.setMutationSortColumn(scoreIdx);
      result.current.setMutationSortAsc(false);
    });
    expect(result.current.displayedRules.map((r) => r.id)).toEqual([2, 1, 3]);

    act(() => {
      result.current.setMutationSortColumn(drugIdx);
      result.current.setMutationSortAsc(true);
    });
    expect(result.current.displayedRules.map((r) => r.id)).toEqual([3, 2, 1]);

    act(() => {
      result.current.setMutationFilter('alpha');
      result.current.setFormulaFilter('combo');
    });

    // Out-of-range sort column safely returns filtered rows without crashing.
    act(() => {
      result.current.setMutationSortColumn(999);
    });
    expect(result.current.displayedRules.map((r) => r.id)).toEqual([2]);

    // Same-DB reload preserves filters/sort AND handles missing payload.data gracefully.
    await act(async () => {
      await result.current.loadMutations('db1');
    });
    expect(result.current.mutationFilter).toBe('alpha');
    expect(result.current.formulaFilter).toBe('combo');
    expect(result.current.mutationSortColumn).toBe(999);
    expect(result.current.rules).toEqual([]);
    expect(result.current.mutationsLoaded).toBe(true);
  });
});
