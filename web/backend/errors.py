"""User-facing error message extraction and normalization for API and job failures."""

from __future__ import annotations

import re

_ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[mK]')
_STRIP_PREFIXES = ('Error: ', 'ValueError: ', 'RuntimeError: ', 'Exception: ', 'OSError: ')

_VCF_HEADERS_MSG = 'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.'
_BAM_FORMAT_MSG = 'Unsupported BAM format. Upload a BGZF-compressed BAM file.'

# Ordered substring rules; each entry matches when every term appears in the lowercased message.
_ERROR_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (('fasta file does not appear to contain valid sequence data',), 'Unsupported FASTA format. Upload a text FASTA file with a header line starting with >.'),
    (('fasta file contains non-text/binary bytes',), 'Unsupported FASTA format. Upload a plain-text FASTA file.'),
    (('fasta file contains invalid sequence characters',), 'Unsupported FASTA format. Sequence lines contain unsupported characters.'),
    (('fasta file contains line', 'longer than'), 'Unsupported FASTA format. Input contains an excessively long line.'),
    (('vcf file does not appear to have valid vcf headers',), _VCF_HEADERS_MSG),
    (('json upload is empty',), 'Unsupported JSON format. Upload a non-empty results JSON file.'),
    (('json upload must be valid utf-8 text',), 'Unsupported JSON format. Upload a UTF-8 encoded JSON file.'),
    (
        ('invalid results json',),
        'Unsupported JSON format. Upload a valid ResistanceProfiler results JSON '
        'with run, variant_result, coverage_gap, formula_rule_hit, and sample_classification sections.',
    ),
    (
        ('project database uuid mismatch',),
        'Project database UUID mismatch. Database updates currently do not allow '
        'regeneration of reports from older database versions.',
    ),
    (('vcf file contains non-text/binary bytes',), 'Unsupported VCF format. Upload a plain-text VCF file.'),
    (('vcf file contains data rows before #chrom header',), _VCF_HEADERS_MSG),
    (('vcf file contains line', 'longer than'), 'Unsupported VCF format. Input contains an excessively long line.'),
    (('vcf file exceeds maximum data row count',), 'Unsupported VCF format. Input contains too many variant rows.'),
    (('bam file does not have valid bgzf/gzip magic signature',), _BAM_FORMAT_MSG),
    (('bam file is too small',), _BAM_FORMAT_MSG),
    (('failed to parse fasta input',), 'Unsupported FASTA format. The FASTA file could not be parsed.'),
    (('failed to parse reference fasta input',), 'Unsupported FASTA reference format. The reference FASTA file could not be parsed.'),
    (('failed to parse vcf input',), 'Unsupported VCF format. The VCF file could not be parsed.'),
    (('failed to parse bam coverage input',), 'Unsupported BAM format. The BAM file could not be parsed for coverage analysis.'),
    (('vcf contig names do not match the uploaded reference fasta',), 'VCF and reference FASTA do not match. Use files derived from the same reference sequence.'),
    (('failed to create bam index',), 'Coverage annotation needs a coordinate-sorted BAM. The server could not create an index for this file.'),
    (('bam reference', 'not found'), 'BAM and reference FASTA do not match. Use files derived from the same reference sequence.'),
    (('no cds matches found',), 'No matches to references in the database found.'),
)


def user_facing_error_message(raw_message: str | None) -> str:
    """Map an internal CLI/validation exception string to a concise user-facing error."""
    if not raw_message:
        return 'The operation failed on the server.'

    message = _extract_primary_error_message(raw_message)
    for prefix in _STRIP_PREFIXES:
        if message.startswith(prefix):
            message = message[len(prefix):]
            break

    lowered = message.lower()
    for terms, friendly in _ERROR_RULES:
        if all(term in lowered for term in terms):
            return friendly
    if message.startswith('Upload failed:'):
        return 'The upload failed on the server.'
    return message


def _extract_primary_error_message(raw_message: str) -> str:
    """Extract the primary error line from traceback or Rich boxed output."""
    cleaned = _ANSI_ESCAPE.sub('', raw_message)
    lines = [line.rstrip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return raw_message.strip()

    boxed = [
        inner
        for line in lines
        if (stripped := line.strip()).startswith('│') and stripped.endswith('│')
        and (inner := stripped.strip('│').strip())
    ]
    return ' '.join(boxed) if boxed else lines[-1].strip()
