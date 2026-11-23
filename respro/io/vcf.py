"""VCF parsing — extract variant calls, allele frequencies, and read depth via pysam."""

from __future__ import annotations

import logging
import math
import re
from pathlib import Path

import pysam

from respro.db.models import VariantCall

logger = logging.getLogger(__name__)


def collect_vcf_chroms(vcf_path: Path) -> set[str]:
    """Return the set of distinct CHROM contigs appearing in variant records."""
    with pysam.VariantFile(str(Path(vcf_path))) as vcf:
        return {record.contig for record in vcf.fetch()}


def collect_vcf_header_contigs(vcf_path: Path) -> set[str]:
    """Return the set of contig names declared in the VCF header."""
    with pysam.VariantFile(str(Path(vcf_path))) as vcf:
        return {str(contig) for contig in vcf.header.contigs}


def parse_vcf(
    vcf_path: Path,
    expected_query_name: str | None = None,
) -> list[VariantCall]:
    """Parse a VCF file via pysam into 0-based ``VariantCall`` records."""
    vcf_path = Path(vcf_path)
    variants: list[VariantCall] = []
    dropped_contig_mismatch = 0
    mismatched_contigs: set[str] = set()

    with pysam.VariantFile(str(vcf_path)) as vcf:
        for record_index, record in enumerate(vcf.fetch()):
            if expected_query_name is not None and record.contig != expected_query_name:
                dropped_contig_mismatch += 1
                mismatched_contigs.add(record.contig)
                continue

            ref = (record.ref or '').upper().replace('U', 'T')
            alts = list(record.alts or [])
            if not ref or not alts:
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

                variants.append(VariantCall(
                    chrom=record.contig,
                    pos=record.pos - 1,
                    ref=ref,
                    alt=alt,
                    allele_freq=_apply_residual_fallback(allele_freqs, alt_idx),
                    depth=depth,
                    filter_status=filter_status,
                    record_index=record_index,
                    alt_index=alt_idx,
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
    """Return canonical FILTER string (``'.'`` when pysam exposes no filter keys)."""
    keys = list(record.filter.keys())
    return ';'.join(str(k) for k in keys) if keys else '.'


def _resolve_record_afs(record: pysam.VariantRecord, n_alts: int) -> list[float | None]:
    """Resolve per-ALT AFs by precedence: INFO/AF -> VAF -> FREQ -> FORMAT/AF -> FORMAT/AD."""
    resolved: list[float | None] = [None] * n_alts

    for key in ('AF', 'VAF', 'FREQ'):
        value = _record_info_get(record, key)
        if value is None:
            continue
        _apply_af_source(record, key, value, n_alts, resolved)
        if all(v is not None for v in resolved):
            return resolved

    sample = _first_sample(record)
    if sample is None:
        return resolved

    sample_af = sample.get('AF')
    if sample_af is not None:
        _apply_af_source(record, 'FORMAT/AF', sample_af, n_alts, resolved)
        if all(v is not None for v in resolved):
            return resolved

    ad_freqs = _extract_ad_afs(record, sample, n_alts)
    if ad_freqs is not None:
        _fill_first_available(resolved, ad_freqs)
    return resolved


def _apply_af_source(
    record: pysam.VariantRecord,
    source: str,
    raw_value: object,
    n_alts: int,
    resolved: list[float | None],
) -> None:
    """Parse one AF array, warn on short/missing entries before padding, and fill ``resolved``."""
    parsed = [_to_float(item) for item in _normalize_to_raw_sequence(raw_value)]
    _warn_allele_array_issue(record, source, parsed, n_alts)
    padded = (parsed + [None] * max(0, n_alts - len(parsed)))[:n_alts]
    _fill_first_available(resolved, padded)


def _extract_ad_afs(
    record: pysam.VariantRecord,
    sample,
    n_alts: int,
) -> list[float | None] | None:
    """Derive per-ALT AFs from ``FORMAT/AD`` (REF + per-ALT depths), warning if short."""
    sample_ad = sample.get('AD')
    if sample_ad is None:
        return None
    ads = _normalize_to_int_sequence(sample_ad)
    if len(ads) < 2:
        return None
    total = sum(d for d in ads if d is not None)
    if total <= 0:
        return None

    ad_freqs: list[float | None] = []
    for idx in range(n_alts):
        depth = ads[idx + 1] if idx + 1 < len(ads) else None
        ad_freqs.append(depth / total if depth is not None else None)
    if len(ads) - 1 < n_alts:
        logger.warning(
            'VCF %s:%s FORMAT/AD has %d allele-depth entries for %d ALTs; '
            'missing ALTs fall back to the residual AF.',
            record.contig or '?', record.pos, len(ads) - 1, n_alts,
        )
    return ad_freqs


def _warn_allele_array_issue(
    record: pysam.VariantRecord,
    source: str,
    parsed: list[float | None],
    n_alts: int,
) -> None:
    """Log warnings when an AF array is shorter than ``n_alts`` or contains missing ``.`` values."""
    locus = f'{record.contig or "?"}:{record.pos}'
    if len(parsed) < n_alts:
        logger.warning(
            'VCF %s %s has %d value(s) for %d ALTs (cardinality mismatch); '
            'uncovered ALTs fall back to the residual AF.',
            locus, source, len(parsed), n_alts,
        )
    n_missing = sum(1 for v in parsed[:n_alts] if v is None)
    if n_missing > 0:
        logger.warning(
            'VCF %s %s has %d missing AF entr(y/es) (VCF "."); '
            'those ALTs fall back to the residual AF.',
            locus, source, n_missing,
        )


def _apply_residual_fallback(resolved: list[float | None], alt_idx: int) -> float:
    """Return ``resolved[alt_idx]``, splitting residual ``max(0, 1 - known)`` across missing slots."""
    value = resolved[alt_idx]
    if value is not None:
        return value
    known_sum = sum(v for v in resolved if v is not None)
    n_missing = sum(1 for v in resolved if v is None)
    residual = max(0.0, 1.0 - known_sum)
    return residual / n_missing if n_missing > 0 else residual


def _fill_first_available(resolved: list[float | None], candidates: list[float | None]) -> None:
    for i, cand in enumerate(candidates):
        if i >= len(resolved):
            break
        if cand is not None and resolved[i] is None:
            resolved[i] = cand


def _is_non_nucleotide_alt(alt: str) -> bool:
    """Return True for symbolic alleles (``<DEL>``), spanning deletions (``*``), or breakends."""
    if not alt:
        return False
    return alt in {'.', '*', '<*>'} or (alt.startswith('<') and alt.endswith('>')) or '[' in alt or ']' in alt


def _extract_depth(record: pysam.VariantRecord) -> int:
    """Extract total read depth from INFO/DP, FORMAT/DP, or FORMAT/AD; return ``-1`` if absent."""
    for key in ('DP', 'DEPTH'):
        parsed = _to_int(_record_info_get(record, key))
        if parsed is not None:
            return parsed

    sample = _first_sample(record)
    if sample is not None:
        parsed_dp = _to_int(sample.get('DP'))
        if parsed_dp is not None:
            return parsed_dp
        sample_ad = sample.get('AD')
        if sample_ad is not None:
            # All-missing AD (e.g. '.,.') carries no depth data and must fall through to -1.
            valid_ads = [d for d in _normalize_to_int_sequence(sample_ad) if d is not None]
            if valid_ads:
                return sum(valid_ads)
    return -1


def _first_sample(record: pysam.VariantRecord):
    sample_names = list(record.samples)
    return record.samples[sample_names[0]] if sample_names else None


def _record_info_get(record: pysam.VariantRecord, key: str) -> object | None:
    try:
        return record.info.get(key)
    except ValueError:
        return None


def _normalize_to_int_sequence(value: object) -> list[int | None]:
    return [_to_int(item) for item in _normalize_to_raw_sequence(value)]


def _normalize_to_raw_sequence(value: object) -> list[object]:
    if isinstance(value, (tuple, list)):
        return list(value)
    if isinstance(value, str) and ',' in value:
        return [item.strip() for item in value.split(',')]
    return [value]


def _to_int(value: object) -> int | None:
    if value is None:
        return None
    token = str(value).strip()
    if not token or token == '.':
        return None
    try:
        parsed = int(float(token))
    except (ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _to_float(value: object) -> float | None:
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
    if not math.isfinite(parsed) or parsed < 0.0:
        return None
    return parsed / 100.0 if percent else parsed
