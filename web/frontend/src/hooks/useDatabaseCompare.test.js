import { StrictMode } from 'react';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { getCompareState, resetCompareState } from '../components/pages/compareSessionState';

vi.mock('../api', async () => {
  const actual = await vi.importActual('../api');
  return {
    ...actual,
    fetchReferenceAccessions: vi.fn(),
    fetchSharedReferences: vi.fn(),
    compareDatabases: vi.fn(),
  };
});

import {
  fetchReferenceAccessions,
  fetchSharedReferences,
  compareDatabases,
} from '../api';
import { useDatabaseCompare } from './useDatabaseCompare';

const DATABASES = [
  { id: 'db1', display_name: 'Alpha' },
  { id: 'db2', display_name: 'Beta' },
  { id: 'db3', display_name: 'Gamma' },
  { id: 'db4', display_name: 'Delta' },
];

const ACCESSIONS = {
  db1: { ACC1: 'Ref A', ACC2: 'Ref B' },
  db2: { ACC1: 'Ref A' },
  db3: { ACC2: 'Ref B' },
  db4: { ACC9: 'Ref Z' },
};

const SHARED_REFS = [
  { accession: 'ACC1', name: 'Ref A', organism: 'MPXV', present_in: ['db1', 'db2'] },
];

const COMPARE_RESULT = {
  reference: { accession: 'ACC1', name: 'Ref A' },
  databases: ['db1', 'db2'],
  metadata_fields: ['drug'],
  venn: [{ region: ['db1', 'db2'], count: 1 }],
  rows: [],
  counts: { total_unique: 1, intersection: 1, per_db: { db1: 1, db2: 1 } },
};

function makeDeferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeAbortError() {
  const err = new Error('The operation was aborted.');
  err.name = 'AbortError';
  return err;
}

describe('useDatabaseCompare', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetCompareState();
    fetchReferenceAccessions.mockResolvedValue({ items: ACCESSIONS, count: 4 });
    fetchSharedReferences.mockResolvedValue({ items: SHARED_REFS, count: 1 });
    compareDatabases.mockResolvedValue(COMPARE_RESULT);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('forwards { signal } to fetchReferenceAccessions, aborts on unmount/databases change, and clears on empty databases', async () => {
    const def1 = makeDeferred();
    const def2 = makeDeferred();
    const signals = [];
    fetchReferenceAccessions.mockImplementation((_ids, options) => {
      signals.push(options?.signal);
      return signals.length === 1 ? def1.promise : def2.promise;
    });

    const { result, rerender, unmount } = renderHook(
      ({ dbs }) => useDatabaseCompare(dbs),
      { initialProps: { dbs: DATABASES } },
    );

    expect(fetchReferenceAccessions).toHaveBeenCalledWith(
      ['db1', 'db2', 'db3', 'db4'],
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
    expect(signals[0].aborted).toBe(false);

    rerender({ dbs: DATABASES.slice(0, 2) });
    expect(signals[0].aborted).toBe(true);
    expect(signals[1].aborted).toBe(false);

    await act(async () => {
      def1.resolve({ items: ACCESSIONS });
      def2.resolve({ items: { db1: { ACC1: 'Ref A' }, db2: { ACC1: 'Ref A' } } });
    });

    await waitFor(() => {
      expect(result.current.optionsForSlot(1).map((d) => d.id)).toEqual(['db1', 'db2']);
    });

    rerender({ dbs: [] });
    expect(result.current.optionsForSlot(0)).toEqual([]);

    const pending = makeDeferred();
    fetchReferenceAccessions.mockImplementationOnce((_ids, options) => {
      signals.push(options?.signal);
      return pending.promise;
    });
    rerender({ dbs: DATABASES });
    expect(signals[2].aborted).toBe(false);

    unmount();
    expect(signals[2].aborted).toBe(true);

    await act(async () => {
      pending.reject(makeAbortError());
    });
  });

  it('keeps canCompare false and runCompare a no-op while sharedRefsLoading is true', async () => {
    const refsDeferred = makeDeferred();
    fetchSharedReferences.mockReturnValue(refsDeferred.promise);

    const { result } = renderHook(() => useDatabaseCompare(DATABASES));
    await waitFor(() => expect(fetchReferenceAccessions).toHaveBeenCalledTimes(1));

    act(() => {
      result.current.handleSlotChange(0, 'db1');
      result.current.handleSlotChange(1, 'db2');
    });

    expect(result.current.sharedRefsLoading).toBe(true);
    expect(result.current.canCompare).toBe(false);

    await act(async () => {
      await result.current.runCompare();
    });
    expect(compareDatabases).not.toHaveBeenCalled();

    await act(async () => {
      refsDeferred.resolve({ items: SHARED_REFS, count: 1 });
    });

    await waitFor(() => {
      expect(result.current.sharedRefsLoading).toBe(false);
      expect(result.current.accession).toBe('ACC1');
      expect(result.current.canCompare).toBe(true);
    });
  });

  it('aborts pending fetchSharedReferences on unmount without caching sharedRefsKey, and refetches on remount', async () => {
    const refsDeferred = makeDeferred();
    const signals = [];
    fetchSharedReferences.mockImplementationOnce((_ids, options) => {
      signals.push(options?.signal);
      return refsDeferred.promise;
    });

    const { result, unmount } = renderHook(() => useDatabaseCompare(DATABASES));
    await waitFor(() => expect(fetchReferenceAccessions).toHaveBeenCalledTimes(1));

    act(() => {
      result.current.handleSlotChange(0, 'db1');
      result.current.handleSlotChange(1, 'db2');
    });

    expect(signals[0].aborted).toBe(false);
    expect(getCompareState().sharedRefsKey).toBe('');

    unmount();
    expect(signals[0].aborted).toBe(true);
    expect(getCompareState().sharedRefsKey).toBe('');

    await act(async () => {
      refsDeferred.resolve({ items: SHARED_REFS, count: 1 });
    });
    expect(getCompareState().sharedRefsKey).toBe('');

    // Remount with restored slots must refetch shared references.
    fetchSharedReferences.mockResolvedValueOnce({ items: SHARED_REFS, count: 1 });
    const remounted = renderHook(() => useDatabaseCompare(DATABASES));
    await waitFor(() => {
      expect(fetchSharedReferences).toHaveBeenCalledTimes(2);
      expect(remounted.result.current.accession).toBe('ACC1');
      expect(getCompareState().sharedRefsKey).toBe('db1,db2');
    });
  });

  it('does not cache failed fetchSharedReferences, caches empty items [], and skips refetch on remount after success', async () => {
    fetchSharedReferences.mockRejectedValueOnce(new Error('DB lookup error'));

    const first = renderHook(() => useDatabaseCompare(DATABASES));
    await waitFor(() => expect(fetchReferenceAccessions).toHaveBeenCalledTimes(1));

    act(() => {
      first.result.current.handleSlotChange(0, 'db1');
      first.result.current.handleSlotChange(1, 'db2');
    });

    await waitFor(() => {
      expect(first.result.current.error).toBe('DB lookup error');
      expect(first.result.current.sharedRefsLoading).toBe(false);
    });
    expect(getCompareState().sharedRefsKey).toBe('');
    first.unmount();

    // Remount retries and succeeds with empty items [] -> caches key so 3rd mount skips fetch.
    fetchSharedReferences.mockResolvedValueOnce({ items: [], count: 0 });
    const second = renderHook(() => useDatabaseCompare(DATABASES));
    await waitFor(() => {
      expect(fetchSharedReferences).toHaveBeenCalledTimes(2);
      expect(second.result.current.sharedRefsLoading).toBe(false);
      expect(getCompareState().sharedRefsKey).toBe('db1,db2');
    });
    second.unmount();

    const third = renderHook(() => useDatabaseCompare(DATABASES));
    await waitFor(() => expect(fetchReferenceAccessions).toHaveBeenCalledTimes(3));
    expect(fetchSharedReferences).toHaveBeenCalledTimes(2);
    third.unmount();
  });

  it('aborts fetchSharedReferences and resets sharedRefsLoading to false when deselecting to < 2 DBs mid-fetch', async () => {
    const refsDeferred = makeDeferred();
    const signals = [];
    fetchSharedReferences.mockImplementation((_ids, options) => {
      signals.push(options?.signal);
      return refsDeferred.promise;
    });

    const { result } = renderHook(() => useDatabaseCompare(DATABASES));
    await waitFor(() => expect(fetchReferenceAccessions).toHaveBeenCalledTimes(1));

    act(() => {
      result.current.handleSlotChange(0, 'db1');
      result.current.handleSlotChange(1, 'db2');
    });
    expect(result.current.sharedRefsLoading).toBe(true);

    // Deselect slot 2 back to empty -> < 2 DBs.
    act(() => {
      result.current.handleSlotChange(1, '');
    });
    expect(signals[0].aborted).toBe(true);
    expect(result.current.sharedRefsLoading).toBe(false);
    expect(result.current.sharedRefs).toEqual([]);
    expect(result.current.accession).toBe('');
    expect(getCompareState().sharedRefsKey).toBe('');

    await act(async () => {
      refsDeferred.resolve({ items: SHARED_REFS, count: 1 });
    });
    expect(result.current.sharedRefs).toEqual([]);
    expect(result.current.sharedRefsLoading).toBe(false);
  });

  it('aborts and discards superseded compareDatabases calls on slot change, setAccession, unmount, and overlapping runCompare', async () => {
    const compareDef1 = makeDeferred();
    const compareDef2 = makeDeferred();
    const compareDef3 = makeDeferred();
    const compareSignals = [];
    compareDatabases.mockImplementation((_ids, _acc, options) => {
      compareSignals.push(options?.signal);
      if (compareSignals.length === 1) return compareDef1.promise;
      if (compareSignals.length === 2) return compareDef2.promise;
      return compareDef3.promise;
    });
    fetchSharedReferences.mockResolvedValue({
      items: [
        ...SHARED_REFS,
        { accession: 'ACC2', name: 'Ref B', present_in: ['db1', 'db2'] },
      ],
      count: 2,
    });

    const { result, unmount } = renderHook(() => useDatabaseCompare(DATABASES));
    await waitFor(() => expect(fetchReferenceAccessions).toHaveBeenCalledTimes(1));

    act(() => {
      result.current.handleSlotChange(0, 'db1');
      result.current.handleSlotChange(1, 'db2');
    });
    await waitFor(() => expect(result.current.canCompare).toBe(true));

    // Call 1 interrupted by setAccession
    let p1;
    act(() => {
      p1 = result.current.runCompare();
    });
    expect(result.current.isBusy).toBe(true);
    expect(compareSignals[0].aborted).toBe(false);

    act(() => {
      result.current.setAccession('ACC2');
    });
    expect(compareSignals[0].aborted).toBe(true);
    expect(result.current.isBusy).toBe(false);

    // Late rejection from call 1 (ignoring signal) is ignored.
    await act(async () => {
      compareDef1.reject(new Error('Stale compare failure'));
      await p1;
    });
    expect(result.current.error).toBe('');
    expect(result.current.response).toBeNull();

    // Call 2 interrupted by handleSlotChange
    let p2;
    act(() => {
      p2 = result.current.runCompare();
    });
    expect(result.current.isBusy).toBe(true);
    act(() => {
      result.current.handleSlotChange(1, '');
    });
    expect(compareSignals[1].aborted).toBe(true);
    expect(result.current.isBusy).toBe(false);

    await act(async () => {
      compareDef2.resolve(COMPARE_RESULT);
      await p2;
    });
    expect(result.current.response).toBeNull();

    // Re-select and test unmount abort
    act(() => {
      result.current.handleSlotChange(1, 'db2');
    });
    await waitFor(() => expect(result.current.canCompare).toBe(true));

    let p3;
    act(() => {
      p3 = result.current.runCompare();
    });
    expect(compareSignals[2].aborted).toBe(false);
    unmount();
    expect(compareSignals[2].aborted).toBe(true);

    await act(async () => {
      compareDef3.resolve(COMPARE_RESULT);
      await p3;
    });
  });

  it('works cleanly under React.StrictMode', async () => {
    const { result } = renderHook(() => useDatabaseCompare(DATABASES), {
      wrapper: StrictMode,
    });
    await waitFor(() => expect(fetchReferenceAccessions).toHaveBeenCalled());

    act(() => {
      result.current.handleSlotChange(0, 'db1');
      result.current.handleSlotChange(1, 'db2');
    });

    await waitFor(() => expect(result.current.canCompare).toBe(true));

    await act(async () => {
      await result.current.runCompare();
    });
    expect(result.current.response).not.toBeNull();
    expect(result.current.isBusy).toBe(false);
  });

  it('handles fetchReferenceAccessions failure fallback, 3-DB intersection, runCompare failure, handleSlotChange reset, venn/filter/exportTsv, and stable handleRegionClick', async () => {
    // 1. fetchReferenceAccessions rejection sets accessionsByDb = null -> optionsForSlot(1) falls back to all non-selected DBs.
    fetchReferenceAccessions.mockRejectedValueOnce(new Error('Accessions unavailable'));
    const { result, unmount } = renderHook(() => useDatabaseCompare(DATABASES));
    await waitFor(() => expect(fetchReferenceAccessions).toHaveBeenCalledTimes(1));

    act(() => {
      result.current.handleSlotChange(0, 'db1');
    });
    expect(result.current.optionsForSlot(1).map((d) => d.id)).toEqual(['db2', 'db3', 'db4']);
    unmount();

    // 2. 3-DB intersection filtering in optionsForSlot(2), non-abort runCompare failure, downstream slot clear, venn/filteredRows/exportTsv.
    resetCompareState();
    fetchReferenceAccessions.mockResolvedValue({
      items: {
        db1: { ACC1: 'Ref A', ACC2: 'Ref B' },
        db2: { ACC1: 'Ref A', ACC2: 'Ref B' },
        db3: { ACC1: 'Ref A' },
        db4: { ACC9: 'Ref Z' },
      },
      count: 4,
    });
    fetchSharedReferences.mockResolvedValue({
      items: [{ accession: 'ACC1', name: 'Ref A', organism: 'MPXV', present_in: ['db1', 'db2', 'db3'] }],
      count: 1,
    });

    const blobs = [];
    const origCreate = URL.createObjectURL;
    const origRevoke = URL.revokeObjectURL;
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    URL.createObjectURL = vi.fn((b) => {
      blobs.push(b);
      return 'blob:tsv';
    });
    URL.revokeObjectURL = vi.fn();

    try {
      const { result: r2 } = renderHook(() => useDatabaseCompare(DATABASES));
      await waitFor(() => expect(fetchReferenceAccessions).toHaveBeenCalledTimes(2));

      const initialHandleRegionClick = r2.current.handleRegionClick;

      act(() => {
        r2.current.handleSlotChange(0, 'db1');
        r2.current.handleSlotChange(1, 'db2');
      });
      expect(r2.current.optionsForSlot(2).map((d) => d.id)).toEqual(['db3']);
      await waitFor(() => expect(r2.current.canCompare).toBe(true));

      // exportTsv before response is a no-op.
      act(() => {
        r2.current.exportTsv();
      });
      expect(blobs).toHaveLength(0);

      // Non-abort runCompare error sets error, clears response, resets isBusy.
      compareDatabases.mockRejectedValueOnce(new Error('Server compare error'));
      await act(async () => {
        await r2.current.runCompare();
      });
      expect(r2.current.error).toBe('Server compare error');
      expect(r2.current.response).toBeNull();
      expect(r2.current.isBusy).toBe(false);

      // handleSlotChange(0, ...) clears downstream slots and error.
      act(() => {
        r2.current.handleSlotChange(0, 'db2');
      });
      expect(r2.current.slots).toEqual(['db2', '', '']);
      expect(r2.current.error).toBe('');

      // Re-select db1 + db2 and run a successful comparison with metadata_fields omitted (fallback to METADATA_FIELDS).
      act(() => {
        r2.current.handleSlotChange(0, 'db1');
        r2.current.handleSlotChange(1, 'db2');
      });
      await waitFor(() => expect(r2.current.canCompare).toBe(true));

      compareDatabases.mockResolvedValueOnce({
        reference: { accession: 'ACC1', name: 'Ref A' },
        databases: [
          { id: 'db1', name: 'Alpha' },
          { id: 'db2', name: 'Beta' },
        ],
        venn: [
          { region: ['db1'], count: 1 },
          { region: ['db2'], count: 0 },
          { region: ['db1', 'db2'], count: 1 },
        ],
        rows: [
          {
            feature: 'UL23',
            position: 41,
            reference: 'A',
            mutation: 'V',
            drug: 'ACV',
            region: ['db1', 'db2'],
            per_db: {
              db1: { drug: 'ACV', phenotype: 'Resistant' },
              db2: { drug: 'ACV', phenotype: 'Sensitive' },
            },
          },
          {
            feature: 'UL54',
            position: 9,
            reference: 'G',
            mutation: 'D',
            drug: 'FOS',
            region: ['db1'],
            per_db: { db1: { drug: 'FOS', phenotype: 'Resistant' }, db2: null },
          },
        ],
        counts: { total_unique: 2, intersection: 1, per_db: { db1: 2, db2: 1 } },
      });

      await act(async () => {
        await r2.current.runCompare();
      });

      // vennRegions excludes count === 0 entries.
      expect(r2.current.vennRegions).toHaveLength(2);
      // metadataFields falls back to all METADATA_FIELDS when metadata_fields is omitted.
      expect(r2.current.metadataFields.length).toBeGreaterThanOrEqual(5);
      expect(r2.current.handleRegionClick).toBe(initialHandleRegionClick);

      // Search by 1-based position (41 -> 42) and regionFilter via handleRegionClick.
      act(() => {
        r2.current.setTableSearch('42');
      });
      expect(r2.current.filteredRows.map((row) => row.feature)).toEqual(['UL23']);

      act(() => {
        r2.current.setTableSearch('');
        r2.current.handleRegionClick(['db1']);
      });
      expect(r2.current.filteredRows.map((row) => row.feature)).toEqual(['UL54']);

      act(() => {
        r2.current.exportTsv();
      });
      expect(blobs).toHaveLength(1);
      const tsvText = await blobs[0].text();
      expect(tsvText).toContain('UL54\tG\t10\tD\tAlpha');
      expect(tsvText).toContain('UL54\tG\t10\tD\tBeta');
    } finally {
      clickSpy.mockRestore();
      URL.createObjectURL = origCreate;
      URL.revokeObjectURL = origRevoke;
    }
  });
});
