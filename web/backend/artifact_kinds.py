"""Single registry of downloadable result artifact kinds and filename helpers.

Registry order is a persisted contract: job records store artifact IDs in this
order. Append new kinds; never reorder existing entries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArtifactKind:
    """One downloadable artifact produced per profiling run."""

    result_field: str
    internal_suffix: str
    download_suffix: str
    media_type: str


REPORT_HTML = ArtifactKind('report_html_path', '.report.html', '.html', 'text/html')
RESULTS_JSON = ArtifactKind('report_json_path', '.results.json', '.json', 'application/json')
REPORT_PDF = ArtifactKind('report_pdf_path', '.report.pdf', '.pdf', 'application/pdf')
RESULTS_TSV = ArtifactKind('report_tsv_path', '.results.tsv', '.tsv', 'text/tab-separated-values')

ARTIFACT_KINDS: tuple[ArtifactKind, ...] = (REPORT_HTML, RESULTS_JSON, REPORT_PDF, RESULTS_TSV)
ARTIFACT_RESULT_FIELDS: tuple[str, ...] = tuple(kind.result_field for kind in ARTIFACT_KINDS)
ARTIFACT_MEDIA_TYPES: dict[str, str] = {kind.result_field: kind.media_type for kind in ARTIFACT_KINDS}

_TIMESTAMP_TOKEN = re.compile(
    r'\.(\d{20})(?=(?:' + '|'.join(re.escape(kind.internal_suffix) for kind in ARTIFACT_KINDS) + r')$)'
)


def artifact_kind_for_path(artifact_path: Path) -> ArtifactKind | None:
    """Return the registered kind whose suffix matches *artifact_path*, if any."""
    text = str(artifact_path)
    for kind in ARTIFACT_KINDS:
        if text.endswith(kind.internal_suffix):
            return kind
    return None


def is_allowed_artifact_path(artifact_path: Path) -> bool:
    """Return True when *artifact_path* carries a registered artifact suffix."""
    return artifact_kind_for_path(artifact_path) is not None


def is_html_report_path(artifact_path: Path) -> bool:
    """Return True when *artifact_path* is the HTML report artifact."""
    return str(artifact_path).endswith(REPORT_HTML.internal_suffix)


def unsupported_artifact_message() -> str:
    """Return the user-facing error listing every accepted suffix."""
    suffixes = ', '.join(sorted(kind.internal_suffix for kind in ARTIFACT_KINDS))
    return f'Unsupported artifact type. Allowed: {suffixes}.'


def strip_artifact_timestamp(filename: str) -> str:
    """Remove the 20-digit timestamp token before a registered artifact suffix."""
    return _TIMESTAMP_TOKEN.sub('', filename)


def sanitize_artifact_stem(input_name: str) -> str:
    """Normalize *input_name* to a stable report-artifact stem."""
    raw_stem = (Path(input_name).stem.strip() or 'profile').removesuffix('.results')
    return ''.join(ch if ch.isalnum() or ch in '._-' else '_' for ch in raw_stem) or 'profile'


def artifact_paths_for_html(output_html_path: Path) -> dict[str, Path]:
    """Return ``{result_field: Path}`` for all registered artifacts sharing *output_html_path*'s stem."""
    html_path = Path(output_html_path)
    if html_path.name.endswith(REPORT_HTML.internal_suffix):
        stem = html_path.name[: -len(REPORT_HTML.internal_suffix)]
    elif html_path.suffix == '.html':
        stem = html_path.stem
    else:
        stem = html_path.name
    return {
        kind.result_field: (html_path if kind is REPORT_HTML else html_path.parent / f'{stem}{kind.internal_suffix}')
        for kind in ARTIFACT_KINDS
    }


def derive_download_filename(artifact_path: Path) -> str:
    """Map an internal artifact filename to its user-facing download name."""
    file_name = strip_artifact_timestamp(artifact_path.name)
    for kind in ARTIFACT_KINDS:
        if file_name.endswith(kind.internal_suffix):
            return file_name[: -len(kind.internal_suffix)] + kind.download_suffix
    return file_name
