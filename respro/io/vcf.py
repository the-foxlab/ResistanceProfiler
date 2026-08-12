"""
VCF parsing — extract variant calls from VCF files.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pysam

from respro.db.models import VariantCall

logger = logging.getLogger(__name__)


def collect_vcf_chroms(vcf_path: Path) -> set[str]:
    """
    Return the set of distinct CHROM names observed in VCF variant records.

    Only CHROMs that appear in parsed variant records are returned; contigs declared
    only in the VCF header (with no variant rows) are ignored. Used by the VCF CLI to
    preflight FASTA header coverage before reference resolution.

    :param vcf_path: path to VCF file
    :return: set of observed CHROM names
    """
    vcf_path = Path(vcf_path)
    chroms: set[str] = set()
    with pysam.VariantFile(str(vcf_path)) as vcf:
        for record in vcf.fetch():
            chroms.add(record.contig)
    return chroms


def parse_vcf(
    vcf_path: Path,
    expected_query_name: str | None = None,
) -> list[VariantCall]:
    """
    Parse a VCF file and return a list of VariantCall objects.

    Uses pysam.VariantFile as the single supported VCF parsing backend.

    :param vcf_path: path to VCF file
    :param expected_query_name: optional query reference name; when set,
        records with CHROM/contig != this value are dropped
    :return: list of VariantCall objects with 0-based positions
    """
    vcf_path = Path(vcf_path)
    variants: list[VariantCall] = []
    dropped_contig_mismatch = 0
    mismatched_contigs: set[str] = set()

    with pysam.VariantFile(str(vcf_path)) as vcf:
        for record in vcf.fetch():
            if expected_query_name is not None and record.contig != expected_query_name:
                dropped_contig_mismatch += 1
                mismatched_contigs.add(record.contig)
                continue

            ref = (record.ref or '').upper().replace('U', 'T')
            if not ref:
                continue

            alts = list(record.alts or [])
            if not alts:
                continue

            filter_status = _extract_filter_status(record)
            depth = _extract_depth(record)
            allele_freqs = _resolve_record_afs(record, len(alts))
            for alt_idx, alt_raw in enumerate(alts):
                alt = (alt_raw or '').upper().replace('U', 'T')
                if not alt or alt in {'.', '*', '<*>'}:
                    continue
                if _is_non_nucleotide_alt(alt):
                    logger.warning(
                        'Skipping non-nucleotide ALT %r at %s:%d (symbolic/breakend alleles '
                        'are not supported)',
                        alt, record.contig, record.pos,
                    )
                    continue

                af = _apply_residual_fallback(allele_freqs, alt_idx)
                variants.append(VariantCall(
                    chrom=record.contig,
                    pos=record.pos - 1,
                    ref=ref,
                    alt=alt,
                    allele_freq=af,
                    depth=depth,
                    filter_status=filter_status,
                ))

    if expected_query_name is None:
        logger.info('Parsed %d variant(s) from %s', len(variants), vcf_path.name)
    else:
        logger.info(
            'Parsed %d variant(s) from %s (dropped %d due to CHROM != %r)',
            len(variants), vcf_path.name, dropped_contig_mismatch, expected_query_name,
        )
        if dropped_contig_mismatch > 0 and not variants:
            found = ', '.join(sorted(mismatched_contigs))
            raise ValueError(
                'VCF contig names do not match the uploaded reference FASTA. '
                f'Expected {expected_query_name!r}, found {found}. '
                'Use files derived from the same reference sequence.'
            )
    return variants


def _extract_filter_status(record: pysam.VariantRecord) -> str:
    """Return canonical filter status string for one VCF record.

    VCF distinguishes ``PASS`` (filters applied and passed) from ``.`` (filtering
    not applied / unknown). pysam exposes an explicit ``PASS`` as a key in
    ``record.filter.keys()`` and an unfiltered record (``FILTER=.``) as an empty
    key set, so an empty key set is mapped to ``.`` rather than ``PASS`` to preserve
    filter provenance.
    """
    if not record.filter.keys():
        return '.'
    return ';'.join(str(key) for key in record.filter.keys())


def _resolve_record_afs(
    record: pysam.VariantRecord,
    n_alts: int,
) -> list[float | None]:
    """
    Resolve a per-ALT allele-frequency list for one record.

    Returns one entry per ALT allele (indexed by ALT position); ``None`` marks an
    allele whose AF could not be resolved from any source. Missing entries (VCF
    ``.`` / pysam ``None``) and indices beyond the end of a short array are kept as
    ``None`` — they are never silently clamped to the last available value. The
    caller is responsible for applying the residual fallback to the ``None`` entries.

    Precedence (first non-``None`` per allele wins):
        INFO/AF → INFO/VAF → INFO/FREQ → FORMAT/AF (first sample) →
        FORMAT/AD-derived (first sample)

    :param record: pysam variant record
    :param n_alts: number of ALT alleles in the record
    :return: list of length ``n_alts`` with a float or ``None`` per ALT
    """
    resolved: list[float | None] = [None] * n_alts

    for key in ('AF', 'VAF', 'FREQ'):
        value = _record_info_get(record, key)
        if value is None:
            continue
        vals = _normalize_to_float_sequence(value, n_alts)
        _warn_allele_array_issue(record, key, vals, n_alts)
        _fill_first_available(resolved, vals)

    sample = _first_sample(record)
    if sample is not None:
        sample_af = sample.get('AF')
        if sample_af is not None:
            vals = _normalize_to_float_sequence(sample_af, n_alts)
            _warn_allele_array_issue(record, 'FORMAT/AF', vals, n_alts)
            _fill_first_available(resolved, vals)

        sample_ad = sample.get('AD')
        if sample_ad is not None:
            ads = _normalize_to_int_sequence(sample_ad)
            if len(ads) >= 2:
                total = sum(ads)
                if total > 0:
                    ad_freqs: list[float | None] = [None] * n_alts
                    for alt_idx in range(n_alts):
                        depth_idx = alt_idx + 1  # AD is REF + per-ALT depths
                        if depth_idx < len(ads):
                            ad_freqs[alt_idx] = ads[depth_idx] / total
                    _fill_first_available(resolved, ad_freqs)
                    # AD is REF + per-ALT; a short AD array lacks depths for later ALTs.
                    if len(ads) - 1 < n_alts:
                        logger.warning(
                            'VCF %s:%s FORMAT/AD has %d allele-depth entries for %d ALTs; '
                            'missing ALTs fall back to the residual AF.',
                            record.contig if record.contig else '?',
                            record.pos,
                            len(ads) - 1,
                            n_alts,
                        )

    return resolved


def _warn_allele_array_issue(
    record: pysam.VariantRecord,
    source: str,
    vals: list[float | None],
    n_alts: int,
) -> None:
    """Log a warning when an allele-specific AF array is short or has missing entries.

    A short array (fewer values than ALTs) or a present-but-missing entry (VCF ``.``,
    normalised to ``None``) means the per-ALT AF for that slot is unknown and will be
    filled by the residual fallback. This is a data-quality signal worth surfacing
    rather than silently absorbing.

    :param record: pysam variant record (used for the locus in the message)
    :param source: human-readable source label, e.g. ``"INFO/AF"`` or ``"FORMAT/AF"``
    :param vals: normalised per-ALT value list (``None`` = missing entry)
    :param n_alts: number of ALT alleles in the record
    """
    n_present = sum(1 for v in vals if v is not None)
    n_missing = len(vals) - n_present
    locus = f'{record.contig if record.contig else "?"}:{record.pos}'
    if len(vals) < n_alts:
        logger.warning(
            'VCF %s %s has %d value(s) for %d ALTs (cardinality mismatch); '
            'uncovered ALTs fall back to the residual AF.',
            locus,
            source,
            len(vals),
            n_alts,
        )
    if n_missing > 0:
        logger.warning(
            'VCF %s %s has %d missing AF entr(y/es) (VCF "."); '
            'those ALTs fall back to the residual AF.',
            locus,
            source,
            n_missing,
        )


def _apply_residual_fallback(
    resolved: list[float | None],
    alt_idx: int,
) -> float:
    """
    Return the AF for one ALT, applying the residual fallback for missing alleles.

    Per VCF semantics the reference allele frequency is ``1 - sum(ALT AF)``. Alleles
    whose AF is ``None`` (missing entry or short array) share the residual
    ``max(0, 1 - sum(known AFs))`` equally, keeping the per-site AF total at exactly
    1.0 (or 0.0 when the known alleles already sum to >= 1). A single missing
    biallelic ALT therefore falls back to ``1.0`` (residual ``1 - 0``), preserving
    the legacy "called variant assumed fully present" behaviour.

    :param resolved: per-ALT resolved AFs (``None`` = missing)
    :param alt_idx: zero-based index of the ALT to resolve
    :return: allele frequency value
    """
    value = resolved[alt_idx]
    if value is not None:
        return value

    # The residual is spread over every None slot in the resolved list, including
    # symbolic/breakend ALTs that downstream code may skip for matching. This keeps
    # the per-site AF total at 1.0 and avoids inflating nucleotide-ALT fractions.
    known_sum = sum(v for v in resolved if v is not None)
    n_missing = sum(1 for v in resolved if v is None)
    residual = max(0.0, 1.0 - known_sum)
    if n_missing <= 0:
        return residual
    return residual / n_missing


def _fill_first_available(
    resolved: list[float | None],
    candidates: list[float | None],
) -> None:
    """Fill ``None`` slots in ``resolved`` from ``candidates`` (first source wins)."""
    for i, cand in enumerate(candidates):
        if i >= len(resolved):
            break
        if cand is None or resolved[i] is not None:
            continue
        resolved[i] = cand


def _is_non_nucleotide_alt(alt: str) -> bool:
    """Return True for symbolic, breakend, and other non-sequence ALT representations.

    ResistanceProfiler annotates nucleotide-level consequences only; symbolic alleles
    (``<DEL>``, ``<INS>``, ``<DUP>``, …), breakend syntax (``C[ref:100[``, ``]ref:100]A``),
    and the spanning-deletion marker ``*`` are rejected at this boundary so they never
    reach translation code with non-ACGTN characters.
    """
    if not alt:
        return False
    if alt in {'.', '*', '<*>'}:
        # '.' and '*' are filtered upstream; kept here for completeness.
        return True
    if alt.startswith('<') and alt.endswith('>'):
        return True
    # Breakend alleles contain '[' or ']' anchoring a remote contig:position.
    if '[' in alt or ']' in alt:
        return True
    return False


def _extract_depth(record: pysam.VariantRecord) -> int:
    """
    Best-effort extraction of read depth.

    Returns -1 when no depth information is present in the VCF record.
    Callers should treat -1 as "no depth data" and skip depth-based filtering
    for those variants rather than silently discarding them.

    :param record: pysam variant record
    :return: read depth value, or -1 if unavailable
    """
    for key in ('DP', 'DEPTH'):
        value = _record_info_get(record, key)
        parsed = _to_int(value)
        if parsed is not None:
            return parsed

    sample = _first_sample(record)
    if sample is not None:
        parsed_dp = _to_int(sample.get('DP'))
        if parsed_dp is not None:
            return parsed_dp

        sample_ad = sample.get('AD')
        if sample_ad is not None:
            ads = _normalize_to_int_sequence(sample_ad)
            if ads:
                return sum(ads)

    # Sentinel: no depth information found
    return -1


def _first_sample(record: pysam.VariantRecord):
    """Return the first sample call in a record, if present."""
    sample_names = list(record.samples)
    if not sample_names:
        return None
    return record.samples[sample_names[0]]


def _record_info_get(record: pysam.VariantRecord, key: str) -> object | None:
    """Safely read one INFO value from pysam record, tolerating incomplete VCF headers."""
    try:
        return record.info.get(key)
    except ValueError:
        return None


def _normalize_to_int_sequence(value: object) -> list[int | None]:
    """Normalize scalar or tuple-like values to a list of ints, preserving None positions.

    Missing entries (pysam ``None`` / VCF ``.``) are kept as ``None`` at their position
    so downstream indexing against the ALT list is not shifted.
    """
    raw = _normalize_to_raw_sequence(value)
    return [_to_int(item) for item in raw]


def _normalize_to_float_sequence(value: object, n_alts: int) -> list[float | None]:
    """Normalize an allele-specific INFO/FORMAT value to a per-ALT float list.

    Preserves ``None`` entries positionally (F3) and pads short arrays to ``n_alts``
    with ``None`` rather than reusing the last value (F4). A scalar is broadcast to a
    one-element list (biallelic case).
    """
    raw = _normalize_to_raw_sequence(value)
    parsed = [_to_float(item) for item in raw]
    if len(parsed) < n_alts:
        parsed.extend([None] * (n_alts - len(parsed)))
    return parsed[:n_alts]


def _normalize_to_raw_sequence(value: object) -> list[object]:
    """Normalize scalar or tuple-like values to a list, preserving None entries."""
    if isinstance(value, (tuple, list)):
        return list(value)
    return [value]


def _to_int(value: object) -> int | None:
    """Parse an integer from a VCF INFO/FORMAT value."""
    if value is None:
        return None
    token = str(value).strip()
    if not token or token == '.':
        return None
    try:
        return int(float(token))
    except ValueError:
        return None


def _to_float(value: object) -> float | None:
    """Parse a float value, accepting optional percent suffixes."""
    if value is None:
        return None
    token = str(value).strip()
    if not token or token == '.':
        return None
    percent = token.endswith('%')
    if percent:
        token = token[:-1]
    match = re.search(r'[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?', token)
    if match is None:
        return None
    parsed = float(match.group(0))
    if percent:
        return parsed / 100.0
    return parsed
