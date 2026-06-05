"""Load bundled CLI/core configuration defaults."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from importlib.resources import files


@dataclass(frozen=True)
class CliTimeoutConfig:
    """Network timeout defaults (seconds) for external metadata lookups."""

    pubchem: int
    pubmed: int
    crossref: int


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
    github_respro_db_api: str
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
    max_gap_distance: int


@dataclass(frozen=True)
class CliConfig:
    """Bundled CLI/core configuration loaded from defaults.toml."""

    timeouts: CliTimeoutConfig
    urls: CliUrlConfig
    parsing: CliParsingConfig
    matching: CliMatchingConfig
    similarity: CliSimilarityConfig
    af_bins: CliAfBinsConfig
    af_bins_fasta: CliAfBinsConfig
    alignment: CliAlignmentConfig


def _load_cli_config() -> CliConfig:
    defaults_path = files('respro.config').joinpath('defaults.toml')
    payload = tomllib.loads(defaults_path.read_text(encoding='utf-8'))

    timeouts = payload['timeouts']
    urls = payload['urls']
    parsing = payload['parsing']
    matching = payload['matching']
    similarity = payload['similarity']

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
            github_respro_db_api=str(urls['github_respro_db_api']),
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
        af_bins=_bins(payload['af_bins']),
        af_bins_fasta=_bins(payload['af_bins_fasta']),
        alignment=CliAlignmentConfig(
            preset=str(alignment['preset']),
            k=int(alignment['k']),
            w=int(alignment['w']),
            best_n=int(alignment['best_n']),
            max_gap_distance=int(alignment['max_gap_distance']),
        ),
    )


CLI_CONFIG = _load_cli_config()
