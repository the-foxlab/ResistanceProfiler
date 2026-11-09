"""Single registry of downloadable result artifact kinds.

Every profiling run emits one artifact per registered kind. The registry drives
artifact validation, single-file media types, download filenames, the
"unsupported artifact type" message, the artifact IDs recorded on job results,
and the derived bundle limit. Adding an export type here is the only place a
new artifact suffix needs to be declared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ArtifactKind:
    """One downloadable artifact produced per profiling run.

    :param result_field: key in a job result payload that carries the artifact ID
    :param internal_suffix: suffix of the stored file under ``results/``
    :param download_suffix: suffix presented to the user on download
    :param media_type: HTTP media type for single-artifact downloads
    """

    result_field: str
    internal_suffix: str
    download_suffix: str
    media_type: str


REPORT_HTML = ArtifactKind('report_html_path', '.report.html', '.html', 'text/html')
RESULTS_JSON = ArtifactKind('report_json_path', '.results.json', '.json', 'application/json')
REPORT_PDF = ArtifactKind('report_pdf_path', '.report.pdf', '.pdf', 'application/pdf')
RESULTS_TSV = ArtifactKind('report_tsv_path', '.results.tsv', '.tsv', 'text/tab-separated-values')

# Registry order is a persisted contract: job records store artifact IDs in this
# order. Append new kinds; never reorder existing entries.
ARTIFACT_KINDS: tuple[ArtifactKind, ...] = (REPORT_HTML, RESULTS_JSON, REPORT_PDF, RESULTS_TSV)

ARTIFACT_RESULT_FIELDS: tuple[str, ...] = tuple(kind.result_field for kind in ARTIFACT_KINDS)

ARTIFACT_MEDIA_TYPES: dict[str, str] = {
    kind.result_field: kind.media_type for kind in ARTIFACT_KINDS
}

_TIMESTAMP_TOKEN = re.compile(
    r'\.(\d{20})(?=(?:' + '|'.join(re.escape(kind.internal_suffix) for kind in ARTIFACT_KINDS) + r')$)'
)


def artifact_kind_for_path(artifact_path: Path) -> ArtifactKind | None:
    """Return the registered kind whose suffix matches the path, if any."""
    for kind in ARTIFACT_KINDS:
        if str(artifact_path).endswith(kind.internal_suffix):
            return kind
    return None


def is_allowed_artifact_path(artifact_path: Path) -> bool:
    """Return True when the path carries a registered artifact suffix."""
    return artifact_kind_for_path(artifact_path) is not None


def is_html_report_path(artifact_path: Path) -> bool:
    """Return True when the path is the report HTML artifact."""
    return str(artifact_path).endswith(REPORT_HTML.internal_suffix)


def unsupported_artifact_message() -> str:
    """Return the user-facing error listing every accepted suffix."""
    suffixes = ', '.join(sorted(kind.internal_suffix for kind in ARTIFACT_KINDS))
    return f'Unsupported artifact type. Allowed: {suffixes}.'


def derive_download_filename(artifact_path: Path) -> str:
    """Map an internal artifact filename to its user-facing download name."""
    file_name = _TIMESTAMP_TOKEN.sub('', artifact_path.name)
    for kind in ARTIFACT_KINDS:
        if file_name.endswith(kind.internal_suffix):
            return file_name[: -len(kind.internal_suffix)] + kind.download_suffix
    return file_name
