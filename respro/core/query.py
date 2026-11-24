"""Query resolution — align user FASTA records to internal CDS features with optional caching."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from respro.config.cli_settings import CLI_CONFIG, CliConfig
from respro.core.alignment import match_query_to_features
from respro.core.coordinates import match_rank
from respro.db.cache import load_cached_mappings, sequence_checksum, store_mappings
from respro.db.features import load_features
from respro.db.models import FeatureMatch
from respro.io.reference import read_fasta

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueryRecord:
    """One user FASTA record and its aligned rule-backed feature matches."""

    query_name: str
    query_sequence: str
    feature_matches: list[FeatureMatch]


def resolve_fasta_query_multi(
    conn: sqlite3.Connection,
    fasta_path: Path,
    *,
    use_cache: bool = True,
    threads: int = 1,
    selected_query_names: set[str] | None = None,
    cfg: CliConfig = CLI_CONFIG,
) -> list[QueryRecord]:
    """Read a single- or multi-record FASTA and align each selected record to ruled CDS features."""
    seqs = read_fasta(fasta_path)
    if not seqs:
        raise ValueError(f'No sequences found in {fasta_path}')

    if selected_query_names is not None:
        selected = {name.strip() for name in selected_query_names}
        seqs = {name: seq for name, seq in seqs.items() if name in selected}
        if not seqs:
            raise ValueError(
                f'None of the selected FASTA record names are present in {fasta_path}. '
                f'Selected={sorted(selected)}.'
            )

    features = load_features(conn)
    if not features:
        raise ValueError('No features with resistance rules in project database')

    cache_allowed = use_cache and cfg.alignment == CLI_CONFIG.alignment
    records: list[QueryRecord] = []
    for query_name, query_seq in seqs.items():
        if not query_seq:
            logger.warning('Skipping empty FASTA record %r in %s', query_name, fasta_path)
            continue
        checksum = sequence_checksum(query_seq)

        matches: list[FeatureMatch] | None = None
        if cache_allowed:
            cached = load_cached_mappings(conn, checksum)
            if cached:
                matches = cached
                logger.info('Using cached feature mappings for %r', query_name)

        if matches is None:
            matches = match_query_to_features(query_seq, features, threads=threads, cfg=cfg)
            if not matches:
                logger.warning(
                    'No CDS matches found for FASTA record %r in %s; dropping record',
                    query_name, fasta_path.name,
                )
                continue
            if cache_allowed:
                store_mappings(conn, checksum, matches)

        records.append(QueryRecord(
            query_name=query_name,
            query_sequence=query_seq,
            feature_matches=matches,
        ))

    if not records:
        raise ValueError(
            f'No FASTA record aligned to any internal reference with rules in {fasta_path.name}'
        )
    return records


def resolve_fasta_query(
    conn: sqlite3.Connection,
    fasta_path: Path,
    *,
    use_cache: bool = True,
    threads: int = 1,
    cfg: CliConfig = CLI_CONFIG,
) -> tuple[str, str, list[FeatureMatch]]:
    """Read and align a single-record consensus FASTA (rejecting multi-record files)."""
    seqs = read_fasta(fasta_path)
    if len(seqs) > 1:
        raise ValueError(
            f'Expected single-record FASTA, got {len(seqs)} records in '
            f'{fasta_path}. Multi-record FASTA is not supported in FASTA mode; '
            'use VCF mode with a multi-record reference FASTA.'
        )

    record = resolve_fasta_query_multi(
        conn, fasta_path, use_cache=use_cache, threads=threads, cfg=cfg,
    )[0]
    return record.query_name, record.query_sequence, record.feature_matches


def pick_best_reference_id(matches: list[FeatureMatch]) -> int:
    """Select the best-scoring internal reference from feature matches using ``match_rank``."""
    if not matches:
        raise ValueError('No FASTA feature matches available for reference selection')
    return min(matches, key=match_rank).feature.reference_id


def select_matches_for_reference(
    matches: list[FeatureMatch],
    reference_id: int,
) -> list[FeatureMatch]:
    """Filter ``matches`` to those belonging to ``reference_id``."""
    selected = [match for match in matches if match.feature.reference_id == reference_id]
    if not selected:
        raise ValueError(f'No FASTA feature matches for reference_id={reference_id}')
    return selected
