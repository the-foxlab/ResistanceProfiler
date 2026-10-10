"""
Query resolution — read a user FASTA, align it to internal CDS annotations, and manage
cached query-to-gene mappings.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from respro.config.cli_settings import CLI_CONFIG, CliConfig
from respro.core.alignment import (
    load_features,
    match_query_to_features,
)
from respro.db.cache import load_cached_mappings, sequence_checksum, store_mappings
from respro.db.models import FeatureMatch
from respro.io.reference import read_fasta

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueryRecord:
    """One user-supplied FASTA record aligned to internal CDS annotations.

    Carries the per-record query identity, sequence, and the feature matches
    produced by aligning that record against the project database's ruled
    features. Multi-record FASTA inputs yield one ``QueryRecord`` per record.
    """

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
    """
    Read a (possibly multi-record) user FASTA and align each record to internal CDS.

    Each FASTA record is aligned independently against the project database's
    rule-backed features (``has_rules = 1``). Records that produce no alignment
    are dropped with a warning; if no record aligns, ``ValueError`` is raised.

    :param conn: project database connection
    :param fasta_path: path to user FASTA (single- or multi-record)
    :param use_cache: if True, reuse/store per-record mapping cache in project DB
    :param threads: number of worker processes for parallel feature alignment
    :param selected_query_names: when provided (VCF mode), only FASTA records whose
        header matches one of these names are aligned/returned; other records are
        ignored entirely (never aligned, cached, or turned into report groups). This
        keeps extra supplied references from introducing irrelevant species/gene
        collisions. ``None`` (default) aligns every record — used by FASTA mode and
        direct callers. The caller is responsible for guaranteeing that every selected
        name is present in the FASTA (the VCF CLI validates this beforehand).
    :return: one ``QueryRecord`` per aligning FASTA record, in input order
    :raises ValueError: if the FASTA is empty or no record aligns to any feature
    """
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

    records: list[QueryRecord] = []
    for query_name, raw_query_seq in seqs.items():
        query_seq = raw_query_seq
        if not query_seq:
            logger.warning('Skipping empty FASTA record %r in %s', query_name, fasta_path)
            continue
        chk = sequence_checksum(query_seq)

        matches: list[FeatureMatch] | None = None
        if use_cache:
            matches = load_cached_mappings(conn, chk)
            if matches is not None:
                logger.info('Using cached feature mappings for %r', query_name)

        if matches is None:
            matches = match_query_to_features(query_seq, features, threads=threads, cfg=cfg)
            if not matches:
                logger.warning(
                    'No CDS matches found for FASTA record %r in %s; dropping record',
                    query_name, fasta_path.name,
                )
                continue
            if use_cache:
                store_mappings(conn, chk, matches)

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
    """
    Read a single-record user FASTA and align to internal CDS annotations.

    Thin wrapper over :func:`resolve_fasta_query_multi` that enforces the
    single-record contract used by FASTA-mode profiling. VCF-mode profiling
    should call :func:`resolve_fasta_query_multi` directly to support
    multi-record reference FASTAs.

    :param conn: project database connection
    :param fasta_path: path to single-record user FASTA
    :param use_cache: if True, reuse/store mapping cache in project DB
    :param threads: number of worker processes for parallel feature alignment
    :return: (query_name, query_sequence, feature_matches)
    :raises ValueError: if the FASTA has more than one record (FASTA-mode contract)
    """
    seqs = read_fasta(fasta_path)
    if len(seqs) > 1:
        raise ValueError(
            f'Expected single-record FASTA, got {len(seqs)} records in '
            f'{fasta_path}. Multi-record FASTA is not supported in FASTA mode; '
            'use VCF mode with a multi-record reference FASTA.'
        )

    records = resolve_fasta_query_multi(
        conn, fasta_path, use_cache=use_cache, threads=threads, cfg=cfg,
    )
    record = records[0]
    return record.query_name, record.query_sequence, record.feature_matches


def pick_best_reference_id(matches: list[FeatureMatch]) -> int:
    """
    Select the most likely internal reference from FASTA feature matches.

    The best single feature match defines the reference. Sorting keys are:
    identity desc, cds_coverage desc, query_coverage desc, feature name asc (lexicographic).

    :param matches: accepted feature matches
    :return: internal reference id
    """
    if not matches:
        raise ValueError('No FASTA feature matches available for reference selection')

    best = min(
        matches,
        key=lambda m: (-m.identity, -m.cds_coverage, -m.query_coverage, m.feature.name),
    )
    return best.feature.reference_id


def select_matches_for_reference(
    matches: list[FeatureMatch],
    reference_id: int,
) -> list[FeatureMatch]:
    """
    Keep only matches belonging to one internal reference.

    :param matches: feature matches from FASTA alignment
    :param reference_id: selected internal reference id
    :return: filtered matches for the selected reference
    """
    selected = [match for match in matches if match.feature.reference_id == reference_id]
    if not selected:
        raise ValueError(f'No FASTA feature matches for reference_id={reference_id}')
    return selected


