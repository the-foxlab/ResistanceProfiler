"""Load bundled TOML configuration defaults with optional user override merging."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints


@dataclass(frozen=True)
class CliTimeoutConfig:
    """Network timeout and retry defaults (seconds) for external metadata lookups."""

    pubchem: int
    pubmed: int
    crossref: int
    genbank_timeout: int
    genbank_max_retries: int
    genbank_backoff_base: float
    genbank_request_interval: float


@dataclass(frozen=True)
class CliUrlConfig:
    """External URL templates used by metadata and maintained-DB lookups."""

    pubchem_compound_page: str
    pubchem_structure_png: str
    pubchem_cid_lookup: str
    pubchem_description: str
    pubchem_title: str
    pubchem_substance_sids: str
    pubchem_substance_record: str
    pubchem_substance_page: str
    wikipedia_summary: str
    ncbi_pubmed_esummary: str
    ncbi_pmc_idconv: str
    crossref_works: str
    ncbi_protein_page: str
    ncbi_nuccore_efetch: str
    github_respro_db_raw: str


@dataclass(frozen=True)
class CliParsingConfig:
    """Token parsing configuration shared by TSV import helpers."""

    doi_prefixes: tuple[str, ...]


@dataclass(frozen=True)
class CliMatchingConfig:
    """Fréchet acceptance policy for boolean formula (combination) rules."""

    min_cooccurrence_combination_fraction: float
    frechet_epsilon: float


@dataclass(frozen=True)
class CliSimilarityConfig:
    """BLOSUM62 score thresholds for amino-acid similarity classification."""

    high: int
    moderate: int


@dataclass(frozen=True)
class CliCodonConfig:
    """Same-codon SNP co-occurrence and BAM read-quality thresholds."""

    min_cooccurrence_codon_fraction: float
    min_read_mapping_quality: int
    bam_base_quality_threshold: int
    frechet_epsilon: float


@dataclass(frozen=True)
class CliAfBinsConfig:
    """Allele-frequency bin boundaries as ``(lower_inclusive, upper_inclusive)``."""

    high: tuple[float, float]
    intermediate: tuple[float, float]
    low: tuple[float, float]

    def as_dict(self) -> dict[str, tuple[float, float]]:
        return {'high': self.high, 'intermediate': self.intermediate, 'low': self.low}


@dataclass(frozen=True)
class CliAlignmentConfig:
    """Minimap2/mappy alignment settings for CDS-to-query mapping."""

    preset: str
    k: int
    w: int
    best_n: int
    gap_open_penalty: int
    match_score: int
    mismatch_penalty: int
    gap_extension_penalty_1: int
    gap_open_penalty_2: int
    gap_extension_penalty_2: int
    intron_junction_tolerance: int


@dataclass(frozen=True)
class CliConfig:
    """Immutable configuration tree loaded from bundled ``defaults.toml``."""

    timeouts: CliTimeoutConfig
    urls: CliUrlConfig
    parsing: CliParsingConfig
    matching: CliMatchingConfig
    similarity: CliSimilarityConfig
    codon: CliCodonConfig
    af_bins: CliAfBinsConfig
    af_bins_fasta: CliAfBinsConfig
    alignment: CliAlignmentConfig


# Authoritative section/key schema derived from CliConfig type hints so TOML override
# validation and dataclass construction stay in lockstep.
_SECTION_DATACLASSES: dict[str, type] = get_type_hints(CliConfig)
_SECTION_FIELD_HINTS: dict[str, dict[str, Any]] = {
    name: get_type_hints(cls) for name, cls in _SECTION_DATACLASSES.items()
}


def _coerce_field(value: Any, field_type: Any) -> Any:
    """Cast a validated TOML value into its dataclass field representation."""
    if get_origin(field_type) is tuple:
        args = get_args(field_type)
        if len(args) == 2 and args[1] is Ellipsis:
            elem_type = args[0]
            return tuple(elem_type(item) for item in value)
        return tuple(t(item) for t, item in zip(args, value))
    return field_type(value)


def _build_cli_config(payload: dict[str, Any]) -> CliConfig:
    """Construct a frozen ``CliConfig`` from a validated TOML payload dict."""
    sections = {
        section: cls(**{
            key: _coerce_field(payload[section][key], hint)
            for key, hint in _SECTION_FIELD_HINTS[section].items()
        })
        for section, cls in _SECTION_DATACLASSES.items()
    }
    return CliConfig(**sections)


def _read_bundled_defaults() -> dict[str, Any]:
    defaults_path = files('respro.config').joinpath('defaults.toml')
    return tomllib.loads(defaults_path.read_text(encoding='utf-8'))


def _load_cli_config() -> CliConfig:
    """Load the bundled ``defaults.toml`` into a ``CliConfig``."""
    return _build_cli_config(_read_bundled_defaults())


def _expected_toml_type(section: str, key: str) -> Any:
    """Return the TOML-level type expected for ``[section] key`` (tuples map to ``list``)."""
    field_type = _SECTION_FIELD_HINTS[section][key]
    return list if get_origin(field_type) in (tuple, list) else field_type


def _merge_section(base: dict[str, Any], override: dict[str, Any], section: str) -> dict[str, Any]:
    """Overlay user keys onto one default section, rejecting unknown keys and type mismatches."""
    merged = dict(base)
    for key, value in override.items():
        if key not in base:
            allowed = ', '.join(sorted(base))
            raise ValueError(f'Unknown config key {key!r} in section [{section}]. Allowed keys: {allowed}.')
        expected = _expected_toml_type(section, key)
        # bool subclasses int in Python; reject booleans where numeric values are expected.
        if isinstance(value, bool) and expected in (int, float):
            raise ValueError(
                f'Type mismatch for config key {key!r} in section [{section}]: '
                f'expected {expected.__name__}, got bool.'
            )
        if not isinstance(value, expected):
            raise ValueError(
                f'Type mismatch for config key {key!r} in section [{section}]: '
                f'expected {expected.__name__}, got {type(value).__name__}.'
            )
        merged[key] = value
    return merged


def load_config_with_overrides(override_path: Path | None = None) -> CliConfig:
    """Return a ``CliConfig`` with optional user TOML overrides merged onto bundled defaults."""
    if override_path is None:
        return CLI_CONFIG

    payload = _read_bundled_defaults()
    override = tomllib.loads(Path(override_path).read_text(encoding='utf-8'))
    merged: dict[str, Any] = {section: dict(values) for section, values in payload.items()}

    for section, section_value in override.items():
        if section not in _SECTION_DATACLASSES:
            allowed = ', '.join(sorted(_SECTION_DATACLASSES))
            raise ValueError(f'Unknown config section [{section}]. Allowed sections: {allowed}.')
        if not isinstance(section_value, dict):
            raise ValueError(
                f'Config section [{section}] must be a table (dict), '
                f'got {type(section_value).__name__}.'
            )
        merged[section] = _merge_section(payload[section], section_value, section)

    return _build_cli_config(merged)


CLI_CONFIG = _load_cli_config()
