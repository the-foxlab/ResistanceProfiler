"""Load bundled CLI/core configuration defaults."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, get_type_hints


@dataclass(frozen=True)
class CliTimeoutConfig:
    """Network timeout defaults (seconds) for external metadata lookups."""

    pubchem: int
    pubmed: int
    crossref: int
    # GenBank/NCBI nuccore efetch timeout, retry count, and exponential-backoff
    # base (seconds; doubled each retry). Promoted from respro/io/maintained_db.py
    # module constants so they join the overridable config surface.
    genbank_timeout: int
    genbank_max_retries: int
    genbank_backoff_base: float


@dataclass(frozen=True)
class CliUrlConfig:
    """External URL templates used by CLI/core metadata integrations."""

    pubchem_compound_page: str
    pubchem_structure_png: str
    pubchem_cid_lookup: str
    pubchem_description: str
    pubchem_title: str
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
    """Matching defaults shared by combination evaluation paths."""

    combination_member_af_threshold: float


@dataclass(frozen=True)
class CliSimilarityConfig:
    """BLOSUM62 score thresholds for amino-acid similarity classification."""

    high: int
    moderate: int


@dataclass(frozen=True)
class CliCodonConfig:
    """Conservative same-codon SNP combination policy (Fréchet intersection)."""

    min_cooccurrence_codon_fraction: float
    min_read_mapping_quality: int
    # Minimum base quality for a read to contribute to BAM depth counts. 0 means
    # accept all bases (preserves pre-promotion behaviour). Raising it drops
    # low-quality bases from per-position depth. Promoted from
    # respro/core/vcf_coverage.py (hardcoded quality_threshold=0).
    bam_base_quality_threshold: int
    # Numerical tolerance for Fréchet-bound acceptance (lower > eps). Promoted
    # from respro/core/combined_snp.py module constant _FRECHET_EPS. Small enough
    # that mathematically exact boundary cases (lower == 0) are stable.
    frechet_epsilon: float


@dataclass(frozen=True)
class CliAfBinsConfig:
    """Allele-frequency classification bin boundaries (lower_inclusive, upper_inclusive)."""

    high: tuple[float, float]
    intermediate: tuple[float, float]
    low: tuple[float, float]

    def as_dict(self) -> dict[str, tuple[float, float]]:
        """Return bins as a label → (lo, hi) dict for use with assign_af_bins."""
        return {
            'high': self.high,
            'intermediate': self.intermediate,
            'low': self.low,
        }


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
    """Bundled CLI/core configuration loaded from defaults.toml."""

    timeouts: CliTimeoutConfig
    urls: CliUrlConfig
    parsing: CliParsingConfig
    matching: CliMatchingConfig
    similarity: CliSimilarityConfig
    codon: CliCodonConfig
    af_bins: CliAfBinsConfig
    af_bins_fasta: CliAfBinsConfig
    alignment: CliAlignmentConfig


def _build_cli_config(payload: dict) -> CliConfig:
    """Construct a frozen :class:`CliConfig` from a parsed TOML payload.

    Shared construction path used by both the bundled-defaults loader
    (:func:`_load_cli_config`) and the override-aware loader
    (:func:`load_config_with_overrides`). The payload is assumed to have already
    been validated for the expected sections/keys and value types by the caller.
    """
    timeouts = payload['timeouts']
    urls = payload['urls']
    parsing = payload['parsing']
    matching = payload['matching']
    similarity = payload['similarity']
    codon = payload['codon']

    alignment = payload['alignment']

    def _bins(section: dict) -> CliAfBinsConfig:
        return CliAfBinsConfig(
            high=tuple(section['high']),
            intermediate=tuple(section['intermediate']),
            low=tuple(section['low']),
        )

    return CliConfig(
        timeouts=CliTimeoutConfig(
            pubchem=int(timeouts['pubchem']),
            pubmed=int(timeouts['pubmed']),
            crossref=int(timeouts['crossref']),
            genbank_timeout=int(timeouts['genbank_timeout']),
            genbank_max_retries=int(timeouts['genbank_max_retries']),
            genbank_backoff_base=float(timeouts['genbank_backoff_base']),
        ),
        urls=CliUrlConfig(
            pubchem_compound_page=str(urls['pubchem_compound_page']),
            pubchem_structure_png=str(urls['pubchem_structure_png']),
            pubchem_cid_lookup=str(urls['pubchem_cid_lookup']),
            pubchem_description=str(urls['pubchem_description']),
            pubchem_title=str(urls['pubchem_title']),
            ncbi_pubmed_esummary=str(urls['ncbi_pubmed_esummary']),
            ncbi_pmc_idconv=str(urls['ncbi_pmc_idconv']),
            crossref_works=str(urls['crossref_works']),
            ncbi_protein_page=str(urls['ncbi_protein_page']),
            ncbi_nuccore_efetch=str(urls['ncbi_nuccore_efetch']),
            github_respro_db_raw=str(urls['github_respro_db_raw']),
        ),
        parsing=CliParsingConfig(
            doi_prefixes=tuple(str(item) for item in parsing['doi_prefixes']),
        ),
        matching=CliMatchingConfig(
            combination_member_af_threshold=float(matching['combination_member_af_threshold']),
        ),
        similarity=CliSimilarityConfig(
            high=int(similarity['high']),
            moderate=int(similarity['moderate']),
        ),
        codon=CliCodonConfig(
            min_cooccurrence_codon_fraction=float(codon['min_cooccurrence_codon_fraction']),
            min_read_mapping_quality=int(codon['min_read_mapping_quality']),
            bam_base_quality_threshold=int(codon['bam_base_quality_threshold']),
            frechet_epsilon=float(codon['frechet_epsilon']),
        ),
        af_bins=_bins(payload['af_bins']),
        af_bins_fasta=_bins(payload['af_bins_fasta']),
        alignment=CliAlignmentConfig(
            preset=str(alignment['preset']),
            k=int(alignment['k']),
            w=int(alignment['w']),
            best_n=int(alignment['best_n']),
            gap_open_penalty=int(alignment['gap_open_penalty']),
            match_score=int(alignment['match_score']),
            mismatch_penalty=int(alignment['mismatch_penalty']),
            gap_extension_penalty_1=int(alignment['gap_extension_penalty_1']),
            gap_open_penalty_2=int(alignment['gap_open_penalty_2']),
            gap_extension_penalty_2=int(alignment['gap_extension_penalty_2']),
            intron_junction_tolerance=int(alignment['intron_junction_tolerance']),
        ),
    )


def _load_cli_config() -> CliConfig:
    """Load the bundled ``defaults.toml`` and build the singleton :class:`CliConfig`."""
    defaults_path = files('respro.config').joinpath('defaults.toml')
    payload = tomllib.loads(defaults_path.read_text(encoding='utf-8'))
    return _build_cli_config(payload)


# Map each CliConfig section field to its sub-dataclass type. This is the
# authoritative source of the overridable section/key surface: any section or
# key not reachable through this tree is rejected by the override loader.
# get_type_hints resolves the string annotations produced by
# ``from __future__ import annotations`` into actual dataclass types.
_SECTION_DATACLASSES: dict[str, type] = {
    name: hint for name, hint in get_type_hints(CliConfig).items()
}


def _expected_toml_type(section: str, key: str) -> Any:
    """Return the TOML-level expected type for a section/key pair.

    Tuple-of-str/tuple-of-float dataclass fields are represented as TOML lists,
    so they validate against ``list`` here (element type checks happen in
    ``_build_cli_config`` via the per-field constructors).
    """
    sub_cls = _SECTION_DATACLASSES[section]
    field_type: Any = get_type_hints(sub_cls)[key]
    # Tuple dataclass fields are TOML lists.
    type_origin = getattr(field_type, '__origin__', None)
    if type_origin in (tuple, list):
        return list
    return field_type


def _deep_merge(base: dict, override: dict, section: str) -> dict:
    """Deep-merge ``override`` onto ``base`` for one section (user wins on collision).

    Nested dicts are merged recursively; lists and scalars are replaced wholesale.
    Unknown keys raise :class:`ValueError`. Type mismatches raise :class:`ValueError`.
    """
    merged = dict(base)
    for key, value in override.items():
        if key not in base:
            allowed = sorted(base.keys())
            raise ValueError(
                f'Unknown config key {key!r} in section [{section}]. '
                f'Allowed keys: {", ".join(allowed)}.'
            )
        expected = _expected_toml_type(section, key)
        # bool is a subclass of int in Python; reject bools where int/float is expected
        # so a literal `true` is not silently accepted as `1`.
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
    """Load bundled ``defaults.toml`` and deep-merge a user override TOML on top.

    (1) Parse the bundled ``defaults.toml`` into a dict. (2) If ``override_path``
    is not None, parse it and deep-merge section-by-section (user dict wins on
    key collision; nested dicts merged recursively; lists replaced wholesale,
    not concatenated). (3) Validate the merged dict against the exact set of
    known sections+keys derived from the :class:`CliConfig` dataclass tree;
    unknown section or key → :class:`ValueError` listing the offending key and
    the allowed keys. (4) Build the frozen :class:`CliConfig` via the shared
    :func:`_build_cli_config` helper. Type-check each value and raise on type
    mismatch. The module-level :data:`CLI_CONFIG` singleton is unchanged.

    :param override_path: optional path to a user TOML file; None returns the
        bundled defaults (field-equal to :data:`CLI_CONFIG`)
    :return: a per-invocation :class:`CliConfig` with overrides applied
    :raises ValueError: on an unknown section/key or a value type mismatch
    """
    defaults_path = files('respro.config').joinpath('defaults.toml')
    payload = tomllib.loads(defaults_path.read_text(encoding='utf-8'))

    if override_path is None:
        return _build_cli_config(payload)

    override = tomllib.loads(Path(override_path).read_text(encoding='utf-8'))

    merged: dict[str, Any] = {}
    for section, section_value in payload.items():
        merged[section] = dict(section_value)

    for section, section_value in override.items():
        if section not in _SECTION_DATACLASSES:
            allowed = sorted(_SECTION_DATACLASSES.keys())
            raise ValueError(
                f'Unknown config section [{section}]. '
                f'Allowed sections: {", ".join(allowed)}.'
            )
        if not isinstance(section_value, dict):
            raise ValueError(
                f'Config section [{section}] must be a table (dict), '
                f'got {type(section_value).__name__}.'
            )
        merged[section] = _deep_merge(payload[section], section_value, section)

    return _build_cli_config(merged)


CLI_CONFIG = _load_cli_config()
