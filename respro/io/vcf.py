"""
VCF parsing — extract variant calls from VCF files.
"""

from __future__ import annotations

import logging
from pathlib import Path

from respro.db.models import VariantCall

logger = logging.getLogger(__name__)


def parse_vcf(vcf_path: Path) -> list[VariantCall]:
    """
    Parse a VCF file and return a list of VariantCall objects.

    Uses a lightweight built-in parser to avoid hard pysam dependency
    for VCF-only workflows. Handles VCF 4.x format.

    :param vcf_path: path to VCF file
    :return: list of VariantCall objects with 0-based positions
    """
    vcf_path = Path(vcf_path)
    variants: list[VariantCall] = []

    with open(vcf_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) < 8:
                continue

            chrom = parts[0]
            pos = int(parts[1]) - 1
            ref = parts[3].upper()
            alt_field = parts[4].upper()
            filt = parts[6]
            info_str = parts[7]

            # Parse INFO field
            info = _parse_info(info_str)

            # Handle multi-allelic sites by splitting on comma
            for alt in alt_field.split(','):
                alt = alt.strip()
                if alt in ('.', '*', '<*>'):
                    continue

                af = _extract_af(info, parts, alt_field, alt)
                depth = _extract_depth(info, parts)

                variants.append(VariantCall(
                    chrom=chrom,
                    pos=pos,
                    ref=ref,
                    alt=alt,
                    allele_freq=af,
                    depth=depth,
                    filter_status=filt,
                ))

    logger.info('Parsed %d variant(s) from %s', len(variants), vcf_path.name)
    return variants


def _parse_info(info_str: str) -> dict[str, str]:
    """
    Parse a VCF INFO column into a dict.

    :param info_str: INFO field string
    :return: dict of INFO key-value pairs
    """
    info: dict[str, str] = {}
    if info_str == '.':
        return info
    for entry in info_str.split(';'):
        if '=' in entry:
            key, val = entry.split('=', 1)
            info[key] = val
        else:
            info[entry] = ''
    return info


def _extract_af(
    info: dict[str, str],
    parts: list[str],
    alt_field: str,
    current_alt: str,
) -> float:
    """
    Best-effort extraction of allele frequency.

    :param info: parsed INFO field
    :param parts: VCF record parts
    :param alt_field: ALT field string
    :param current_alt: current ALT allele
    :return: allele frequency value
    """
    # Try INFO/AF
    for key in ('AF', 'VAF', 'FREQ'):
        if key in info:
            try:
                vals = info[key].split(',')
                alt_idx = alt_field.split(',').index(current_alt)
                return float(vals[min(alt_idx, len(vals) - 1)])
            except (ValueError, IndexError):
                pass

    # Try FORMAT/AD -> compute AF
    if len(parts) >= 10:
        fmt_keys = parts[8].split(':')
        fmt_vals = parts[9].split(':')
        fmt = dict(zip(fmt_keys, fmt_vals))
        if 'AD' in fmt:
            try:
                ads = [int(x) for x in fmt['AD'].split(',')]
                total = sum(ads)
                if total > 0:
                    alt_idx = alt_field.split(',').index(current_alt) + 1
                    return ads[min(alt_idx, len(ads) - 1)] / total
            except (ValueError, IndexError):
                pass
        if 'AF' in fmt:
            try:
                return float(fmt['AF'].split(',')[0])
            except (ValueError, IndexError):
                pass

    # A called variant with no extractable AF is assumed fully present
    return 1.0


def _extract_depth(info: dict[str, str], parts: list[str]) -> int:
    """
    Best-effort extraction of read depth.

    :param info: parsed INFO field
    :param parts: VCF record parts
    :return: read depth value
    """
    for key in ('DP', 'DEPTH'):
        if key in info:
            try:
                return int(info[key])
            except ValueError:
                pass
    # Try FORMAT/DP
    if len(parts) >= 10:
        fmt_keys = parts[8].split(':')
        fmt_vals = parts[9].split(':')
        fmt = dict(zip(fmt_keys, fmt_vals))
        if 'DP' in fmt:
            try:
                return int(fmt['DP'])
            except ValueError:
                pass
    return 0
