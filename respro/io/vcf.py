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

    with pysam.VariantFile(str(vcf_path)) as vcf:
        for record in vcf.fetch():
            if expected_query_name is not None and record.contig != expected_query_name:
                dropped_contig_mismatch += 1
                continue

            ref = (record.ref or '').upper().replace('U', 'T')
            if not ref:
                continue

            alts = list(record.alts or [])
            if not alts:
                continue

            filter_status = _extract_filter_status(record)
            depth = _extract_depth(record)
            for alt_idx, alt_raw in enumerate(alts):
                alt = (alt_raw or '').upper().replace('U', 'T')
                if not alt or alt in {'.', '*', '<*>'}:
                    continue

                af = _extract_af(record, alt_idx)
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
    return variants


def _extract_filter_status(record: pysam.VariantRecord) -> str:
    """Return canonical filter status string for one VCF record."""
    if not record.filter.keys():
        return 'PASS'
    return ';'.join(str(key) for key in record.filter.keys())


def _extract_af(
    record: pysam.VariantRecord,
    alt_idx: int,
) -> float:
    """
    Best-effort extraction of allele frequency.

    :param record: pysam variant record
    :param alt_idx: zero-based index of ALT allele in this record
    :return: allele frequency value
    """
    for key in ('AF', 'VAF', 'FREQ'):
        value = _record_info_get(record, key)
        if value is None:
            continue

        vals = _normalize_to_str_sequence(value)
        if not vals:
            continue
        parsed = _to_float(vals[min(alt_idx, len(vals) - 1)])
        if parsed is not None:
            return parsed

    sample = _first_sample(record)
    if sample is not None:
        sample_af = sample.get('AF')
        if sample_af is not None:
            vals = _normalize_to_str_sequence(sample_af)
            if vals:
                parsed = _to_float(vals[min(alt_idx, len(vals) - 1)])
                if parsed is not None:
                    return parsed

        sample_ad = sample.get('AD')
        if sample_ad is not None:
            ads = _normalize_to_int_sequence(sample_ad)
            if len(ads) >= 2:
                total = sum(ads)
                if total > 0:
                    sample_alt_idx = min(alt_idx + 1, len(ads) - 1)
                    return ads[sample_alt_idx] / total

    # A called variant with no extractable AF is assumed fully present
    return 1.0


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


def _normalize_to_str_sequence(value: object) -> list[str]:
    """Normalize scalar or tuple-like values to a list of strings."""
    if isinstance(value, (tuple, list)):
        return [str(v) for v in value if v is not None]
    return [str(value)]


def _normalize_to_int_sequence(value: object) -> list[int]:
    """Normalize scalar or tuple-like values to a list of ints where possible."""
    values = _normalize_to_str_sequence(value)
    parsed: list[int] = []
    for token in values:
        parsed_int = _to_int(token)
        if parsed_int is not None:
            parsed.append(parsed_int)
    return parsed


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
