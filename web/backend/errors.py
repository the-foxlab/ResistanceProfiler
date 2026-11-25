"""User-facing error message extraction and normalization for API and job failures."""

from __future__ import annotations

import re

_ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*[mK]')
_GENERIC_ERROR = 'The operation failed on the server.'
_SAFE_STRIP_PREFIXES = ('Error: ', 'ValueError: ')
_UNSAFE_STRIP_PREFIXES = ('RuntimeError: ', 'Exception: ', 'OSError: ')

_TRACEBACK_MARKER_RE = re.compile(
    r'traceback \(most recent call last\):|^\s*file ".*", line \d+',
    re.IGNORECASE | re.MULTILINE,
)
_STACK_FRAME_LINE_RE = re.compile(
    r'^(?:traceback \(most recent call last\):|file ".*", line \d+)',
    re.IGNORECASE,
)
_EXCEPTION_TOKEN_RE = re.compile(
    r'(?:^|[\s:(])'
    r'(?:[A-Za-z_][\w.]*(?:Error|Exception|Warning)'
    r'|(?:[A-Za-z_][\w.]*\.)?(?:TimeoutExpired|SystemExit|StopIteration|StopAsyncIteration|KeyboardInterrupt|GeneratorExit|BaseExceptionGroup|ExceptionGroup))'
    r':(?:\s|$)'
)
_PYTHON_REPR_RE = re.compile(
    r"<(?:[^>\n]+ at 0x[0-9a-f]+|bound method [^>\n]+|class '[^'\n]+')>",
    re.IGNORECASE,
)
_POSIX_PATH_RE = re.compile(
    r'(?:^|(?<=[\s\'"(=,:`\[<{;*|>]))'
    r'(/(?!/)(?:[^\s\'",:;)\]}>`]+/)+[^\s\'",:;)\]}>`]+'
    r'|/(?!/)[A-Za-z._-][A-Za-z0-9._-]{1,}(?:/[^\s\'",:;)\]}>`]*)?'
    r'|\.\.?/[^\s\'",:;)\]}>`]+'
    r'|~/[^\s\'",:;)\]}>`]+)'
)
_WINDOWS_PATH_RE = re.compile(
    r'(?:^|(?<=[\s\'"(=,:`\[<{;*|>]))'
    r'(?:[A-Za-z]:(?:\\|/(?!/))[^\s\'",;)\]}>`]+'
    r'|\\\\[^\s\'",;)\]}>`]+\\[^\s\'",;)\]}>`]+'
    r'|\.\.?\\[^\s\'",;)\]}>`]+)'
)
_FORWARD_UNC_PATH_RE = re.compile(
    r'(?:^|(?<=[\s\'"(=,`\[<{;*|>]))//[^\s\'",;)\]}>`]+/[^\s\'",;)\]}>`]+'
)

_SQLITE_MARKERS = (
    'sqlite',
    'no such table',
    'no such column',
    'has no column named',
    'no such index',
    'no such trigger',
    'no such view',
    'no such module',
    'database disk image is malformed',
    'file is not a database',
    'unable to open database file',
    'sql logic error',
    'syntax error',
    'constraint failed',
    'unique constraint failed',
    'not null constraint failed',
    'foreign key constraint failed',
    'check constraint failed',
    'database is locked',
    'database schema has changed',
    'attempt to write a readonly database',
    'database or disk is full',
    'database is full',
    'cannot start a transaction',
    'cannot commit',
    'cannot rollback',
)
_INTERNAL_MARKERS = (
    '[errno ',
    'traceback (most recent call last)',
    'command exited with code ',
    'file://',
    'invalid literal for int()',
    'invalid literal for ',
    'not enough values to unpack',
    'too many values to unpack',
    'could not convert string to float',
    'math domain error',
    'port could not be cast to integer value',
    'expecting value: line ',
    'badly formed hexadecimal uuid string',
    'i/o operation on closed file',
)

_VCF_HEADERS_MSG = 'Unsupported VCF format. Upload a VCF with standard headers such as ##fileformat and #CHROM.'
_BAM_FORMAT_MSG = 'Unsupported BAM format. Upload a BGZF-compressed BAM file.'

# Ordered substring rules; each entry matches when every term appears in the lowercased message.
_ERROR_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (('fasta file does not appear to contain valid sequence data',), 'Unsupported FASTA format. Upload a text FASTA file with a header line starting with >.'),
    (('fasta file contains non-text/binary bytes',), 'Unsupported FASTA format. Upload a plain-text FASTA file.'),
    (('fasta file contains invalid sequence characters',), 'Unsupported FASTA format. Sequence lines contain unsupported characters.'),
    (('fasta file contains line', 'longer than'), 'Unsupported FASTA format. Input contains an excessively long line.'),
    (('no sequences found in',), 'Unsupported FASTA format. No sequences were found in the FASTA file.'),
    (
        ('expected single-record fasta', 'multi-record fasta is not supported'),
        'Multi-record FASTA is not supported in FASTA mode. Use VCF mode with a multi-record reference FASTA.',
    ),
    (
        ('none of the selected fasta record names are present',),
        'Selected reference FASTA record was not found in the uploaded reference FASTA.',
    ),
    (('no fasta record aligned to any internal reference',), 'No matches to references in the database found.'),
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
    (('no example fasta is stored in project database',), 'No example FASTA is stored in the selected project database.'),
    (('respro cli subprocess timed out',), 'The analysis job timed out on the server.'),
)


def _is_unsafe_or_internal(
    raw_message: str,
    candidate: str,
    stripped_safe: bool,
    stripped_unsafe: bool,
) -> bool:
    """Return True when *candidate* or *raw_message* appears to expose internal server state."""
    if not candidate.strip():
        return True
    if _STACK_FRAME_LINE_RE.match(candidate):
        return True
    if _TRACEBACK_MARKER_RE.search(raw_message) and (stripped_unsafe or not stripped_safe):
        return True
    if stripped_unsafe:
        return True
    if _EXCEPTION_TOKEN_RE.search(candidate):
        return True

    lowered = candidate.lower()
    if any(marker in lowered for marker in _SQLITE_MARKERS):
        return True
    if any(marker in lowered for marker in _INTERNAL_MARKERS):
        return True
    if _PYTHON_REPR_RE.search(candidate):
        return True
    if _POSIX_PATH_RE.search(candidate) or _WINDOWS_PATH_RE.search(candidate) or _FORWARD_UNC_PATH_RE.search(candidate):
        return True
    return False


def user_facing_error_message(raw_message: str | None) -> str:
    """Map an internal CLI/validation exception string to a concise user-facing error."""
    if not raw_message or not raw_message.strip():
        return _GENERIC_ERROR

    message = _extract_primary_error_message(raw_message)
    stripped_safe = False
    stripped_unsafe = False
    candidate = message
    while True:
        matched_prefix = False
        for prefix in _SAFE_STRIP_PREFIXES:
            if candidate.startswith(prefix):
                stripped_safe = True
                candidate = candidate[len(prefix):]
                matched_prefix = True
                break
        if matched_prefix:
            continue
        for prefix in _UNSAFE_STRIP_PREFIXES:
            if candidate.startswith(prefix):
                stripped_unsafe = True
                candidate = candidate[len(prefix):]
                matched_prefix = True
                break
        if not matched_prefix:
            break

    lowered = candidate.lower()
    for terms, friendly in _ERROR_RULES:
        if all(term in lowered for term in terms):
            return friendly
    if candidate.startswith('Upload failed:'):
        return 'The upload failed on the server.'
    if _is_unsafe_or_internal(raw_message, candidate, stripped_safe, stripped_unsafe):
        return _GENERIC_ERROR
    return candidate


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
