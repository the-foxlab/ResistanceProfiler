"""
Resistance rule matching — load rules from the project database and match against annotated variants.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from respro.db.models import (
    AnnotatedVariant,
    ComboRuleHit,
    Publication,
    ResistanceRule,
    ResistanceRuleSet,
    ResistanceRuleSetMember,
)
from respro.db.rules_import import _load_resistance_rules

logger = logging.getLogger(__name__)


def import_rules_with_summary(
    conn: sqlite3.Connection,
    project_id: int,
    rules_tsv: Path,
    *,
    additional_info: bool,
) -> dict[str, int]:
    """
    Import rules TSV and return inserted row counts.

    :param conn: open project DB connection
    :param project_id: current project id
    :param rules_tsv: rules TSV path
    :param additional_info: enable external metadata lookups during import
    :return: summary counts for inserted rows
    """
    before_sets = int(conn.execute('SELECT COUNT(*) FROM resistance_rule_set').fetchone()[0])
    before_members = int(conn.execute('SELECT COUNT(*) FROM resistance_rule_set_member').fetchone()[0])

    single_rules = _load_resistance_rules(
        conn,
        project_id,
        rules_tsv,
        additional_info=additional_info,
    )

    after_sets = int(conn.execute('SELECT COUNT(*) FROM resistance_rule_set').fetchone()[0])
    after_members = int(conn.execute('SELECT COUNT(*) FROM resistance_rule_set_member').fetchone()[0])

    return {
        'single_rules': int(single_rules),
        'combo_rule_sets': max(0, after_sets - before_sets),
        'combo_rule_set_members': max(0, after_members - before_members),
    }


def validate_rules_tsv(
    conn: sqlite3.Connection,
    project_id: int,
    rules_tsv: Path,
) -> dict[str, int]:
    """
    Validate a rules TSV by running the real import pipeline in a rolled-back savepoint.

    :param conn: open project DB connection
    :param project_id: current project id
    :param rules_tsv: rules TSV path
    :return: summary counts produced by the validation pass
    """
    conn.execute('SAVEPOINT rules_validate')
    try:
        summary = import_rules_with_summary(
            conn,
            project_id,
            rules_tsv,
            additional_info=False,
        )
    finally:
        conn.execute('ROLLBACK TO SAVEPOINT rules_validate')
        conn.execute('RELEASE SAVEPOINT rules_validate')
    return summary


def load_rules(conn: sqlite3.Connection, reference_id: int) -> list[ResistanceRule]:
    """
    Load all resistance rules for genes belonging to a reference.

    :param conn: SQLite database connection
    :param reference_id: ID of the reference
    :return: list of ResistanceRule objects
    """
    rows = conn.execute(
        """
        SELECT
            rr.id, g.name AS gene_name, rr.gene_id,
            d.name AS drug_name, rr.drug_id,
            d.pubchem_url, d.description,
            rr.reference_identifier,
            rr.position, rr.reference, rr.mutation,
            rr.phenotype, rr.clinical_phenotype, rr.ic50, rr.fold_ic50, rr.source, rr.comment
        FROM resistance_rule rr
        JOIN gene g ON g.id = rr.gene_id
        JOIN drug d ON d.id = rr.drug_id
        WHERE g.reference_id = ?
        ORDER BY g.name, rr.position
        """,
        (reference_id,),
    ).fetchall()

    rules = [_rule_from_row(r) for r in rows]

    if rules:
        _attach_publications_to_rules(conn, rules)

    logger.info('Loaded %d resistance rule(s)', len(rules))
    return rules


def load_rule_sets(conn: sqlite3.Connection, reference_id: int) -> list[ResistanceRuleSet]:
    """
    Load all combination resistance rule sets for genes belonging to a reference.

    Only rule sets where at least one member gene belongs to the given reference
    are returned. Members from other references are included in full so that
    cross-reference sets can be evaluated during matching.

    :param conn: SQLite database connection
    :param reference_id: ID of the reference
    :return: list of ResistanceRuleSet objects with populated members
    """
    set_rows = conn.execute(
        """
        SELECT DISTINCT
            rs.id, rs.drug_id, d.name AS drug_name,
            d.pubchem_url, d.description,
            rs.phenotype, rs.clinical_phenotype, rs.ic50, rs.fold_ic50,
            rs.source, rs.group_name, rs.comment
        FROM resistance_rule_set rs
        JOIN drug d ON d.id = rs.drug_id
        JOIN resistance_rule_set_member rsm ON rsm.rule_set_id = rs.id
        JOIN gene g ON g.id = rsm.gene_id
        WHERE g.reference_id = ?
        ORDER BY rs.id
        """,
        (reference_id,),
    ).fetchall()

    if not set_rows:
        return []

    rule_sets: dict[int, ResistanceRuleSet] = {r['id']: _rule_set_from_row(r) for r in set_rows}

    set_id_placeholders = ','.join('?' * len(rule_sets))
    member_rows = conn.execute(
        f"""
        SELECT
            rsm.id, rsm.rule_set_id, g.name AS gene_name, rsm.gene_id,
            rsm.reference_identifier, rsm.position, rsm.reference, rsm.mutation
        FROM resistance_rule_set_member rsm
        JOIN gene g ON g.id = rsm.gene_id
        WHERE rsm.rule_set_id IN ({set_id_placeholders})
        ORDER BY rsm.rule_set_id, rsm.id
        """,
        list(rule_sets.keys()),
    ).fetchall()

    for m in member_rows:
        rule_sets[m['rule_set_id']].members.append(
            ResistanceRuleSetMember(
                id=m['id'],
                rule_set_id=m['rule_set_id'],
                gene_name=m['gene_name'],
                gene_id=m['gene_id'],
                reference_identifier=m['reference_identifier'] or '',
                position=m['position'],
                reference=m['reference'] or '',
                mutation=m['mutation'],
            )
        )

    _attach_publications_to_rule_sets(conn, list(rule_sets.values()))

    logger.info('Loaded %d combination rule set(s)', len(rule_sets))
    return list(rule_sets.values())


def match_rules(
    annotations: list[AnnotatedVariant],
    rules: list[ResistanceRule],
) -> list[AnnotatedVariant]:
    """
    Match annotated variants against resistance rules.

    Mutates the ``rule_matches`` attribute of each AnnotatedVariant in place
    and returns the same list.

    :param annotations: list of annotated variants
    :param rules: list of resistance rules to match against
    :return: the same annotations list with rule_matches populated
    """
    # Build a lookup by gene and codon position.
    rule_index: dict[tuple[str, int], list[ResistanceRule]] = {}
    for rule in rules:
        key = (rule.gene_name, rule.position)
        rule_index.setdefault(key, []).append(rule)

    hit_count = 0
    anchor_warning_cache: set[str] = set()
    for ann in annotations:
        if not ann.gene_name or not ann.alt_aa or ann.consequence == 'synonymous':
            continue

        key = (ann.gene_name, ann.codon_pos)
        candidates = rule_index.get(key, [])
        for rule in candidates:
            anchor_warning = _indel_anchor_mismatch_warning(
                rule_reference=rule.reference,
                rule_mutation=rule.mutation,
                ann_ref=ann.ref_aa,
                ann_alt=ann.alt_aa,
                ann_consequence=ann.consequence,
                gene_name=ann.gene_name,
                codon_pos=ann.codon_pos,
            )
            if anchor_warning and anchor_warning not in anchor_warning_cache:
                logger.warning(anchor_warning)
                anchor_warning_cache.add(anchor_warning)

            if _matches_rule_alleles(
                reference=rule.reference,
                mutation=rule.mutation,
                ann_ref=ann.ref_aa,
                ann_alt=ann.alt_aa,
                ann_consequence=ann.consequence,
            ):
                ann.rule_matches.append(rule)
                hit_count += 1

    logger.info('Matched %d rule hit(s) across %d annotation(s)', hit_count, len(annotations))
    return annotations


def match_rule_sets(
    annotations: list[AnnotatedVariant],
    rule_sets: list[ResistanceRuleSet],
    snp_combine_af_threshold: float = 0.75,
) -> list[ComboRuleHit]:
    """
    Match annotated variants against combination resistance rule sets.

    A rule set fires only when every member mutation is present in the annotated
    variant list.

    :param annotations: list of annotated variants
    :param rule_sets: list of ResistanceRuleSet objects with populated members
    :param snp_combine_af_threshold: strict AF threshold used for combo-member support;
        only annotations with AF > threshold can satisfy a combo member
    :return: list of ComboRuleHit for every rule set that fired
    """
    if not rule_sets:
        return []

    # Build lookup from (gene_name, codon_pos) -> list[AnnotatedVariant].
    # Synonymous and low-AF variants cannot satisfy a resistance rule member.
    present: dict[tuple[str, int], list[AnnotatedVariant]] = {}
    for ann in annotations:
        if not ann.gene_name or not ann.alt_aa or ann.consequence == 'synonymous':
            continue
        if ann.variant.allele_freq <= snp_combine_af_threshold:
            continue
        present.setdefault((ann.gene_name, ann.codon_pos), []).append(ann)

    hits: list[ComboRuleHit] = []
    anchor_warning_cache: set[str] = set()
    for rule_set in rule_sets:
        contributing: list[AnnotatedVariant] = []
        all_matched = True
        for member in rule_set.members:
            ann = _pick_matching_member_annotation(
                present.get((member.gene_name, member.position), []),
                member.reference,
                member.mutation,
                member.gene_name,
                member.position,
                anchor_warning_cache,
            )
            if ann is None:
                all_matched = False
                break
            contributing.append(ann)

        if all_matched and contributing:
            hits.append(ComboRuleHit(rule_set=rule_set, matched_variants=contributing))

    logger.info(
        'Matched %d combination rule set hit(s) across %d annotation(s)',
        len(hits),
        len(annotations),
    )
    return hits


def _publication_from_row(row: sqlite3.Row) -> Publication:
    """Build one Publication object from a SQLite row."""
    return Publication(
        id=int(row['id']),
        doi=row['doi'] or '',
        title=row['title'] or '',
        pubmed_id=row['pubmed_id'] or '',
        raw_input=row['raw_input'] or '',
    )


def _fetch_publications_by_owner(
    conn: sqlite3.Connection,
    owner_ids: list[int],
    *,
    link_table: str,
    owner_column: str,
) -> dict[int, list[Publication]]:
    """Fetch publications grouped by owner id (rule or rule_set)."""
    if not owner_ids:
        return {}

    placeholders = ','.join('?' * len(owner_ids))
    rows = conn.execute(
        f'SELECT lp.{owner_column} AS owner_id, p.id, p.doi, p.title, p.pubmed_id, p.raw_input '
        f'FROM {link_table} lp '
        f'JOIN publication p ON p.id = lp.publication_id '
        f'WHERE lp.{owner_column} IN ({placeholders})',
        owner_ids,
    ).fetchall()

    grouped: dict[int, list[Publication]] = {}
    for row in rows:
        grouped.setdefault(int(row['owner_id']), []).append(_publication_from_row(row))
    return grouped


def _rule_from_row(row: sqlite3.Row) -> ResistanceRule:
    """Build one ResistanceRule object from a SQLite row."""
    return ResistanceRule(
        id=row['id'],
        gene_name=row['gene_name'],
        gene_id=row['gene_id'],
        drug_name=row['drug_name'],
        drug_id=row['drug_id'],
        reference_identifier=row['reference_identifier'] or '',
        position=row['position'],
        reference=row['reference'] or '',
        mutation=row['mutation'],
        phenotype=row['phenotype'],
        clinical_phenotype=row['clinical_phenotype'] or 'unknown',
        ic50=row['ic50'] or '',
        fold_ic50=row['fold_ic50'] or '',
        source=row['source'] or '',
        comment=row['comment'] or '',
        pubchem_url=row['pubchem_url'] or '',
        description=row['description'] or '',
    )


def _rule_set_from_row(row: sqlite3.Row) -> ResistanceRuleSet:
    """Build one ResistanceRuleSet object from a SQLite row."""
    return ResistanceRuleSet(
        id=row['id'],
        drug_name=row['drug_name'],
        drug_id=row['drug_id'],
        phenotype=row['phenotype'],
        clinical_phenotype=row['clinical_phenotype'] or 'unknown',
        ic50=row['ic50'] or '',
        fold_ic50=row['fold_ic50'] or '',
        source=row['source'] or '',
        group_name=row['group_name'] or '',
        comment=row['comment'] or '',
        pubchem_url=row['pubchem_url'] or '',
        description=row['description'] or '',
    )


def _attach_publications_to_rules(
    conn: sqlite3.Connection,
    rules: list[ResistanceRule],
) -> None:
    """
    Batch-load publications for a list of rules and assign them in place.

    :param conn: SQLite database connection
    :param rules: list of ResistanceRule objects to enrich
    """
    rule_ids = [r.id for r in rules]
    pubs_by_rule = _fetch_publications_by_owner(
        conn,
        rule_ids,
        link_table='rule_publication',
        owner_column='rule_id',
    )
    for rule in rules:
        rule.publications = pubs_by_rule.get(rule.id, [])


def _attach_publications_to_rule_sets(
    conn: sqlite3.Connection,
    rule_sets: list[ResistanceRuleSet],
) -> None:
    """
    Batch-load publications for a list of rule sets and assign them in place.

    :param conn: SQLite database connection
    :param rule_sets: list of ResistanceRuleSet objects to enrich
    """
    set_ids = [rs.id for rs in rule_sets]
    pubs_by_set = _fetch_publications_by_owner(
        conn,
        set_ids,
        link_table='rule_set_publication',
        owner_column='rule_set_id',
    )
    for rs in rule_sets:
        rs.publications = pubs_by_set.get(rs.id, [])


def _matches_rule_alleles(
    *,
    reference: str,
    mutation: str,
    ann_ref: str,
    ann_alt: str,
    ann_consequence: str = '',
) -> bool:
    """Compare one rule allele pair with one annotation allele pair."""
    if ann_consequence == 'frameshift':
        return _is_frameshift_token(mutation) and _is_frameshift_token(ann_alt)

    # In-frame insertion-like rules are matched by inserted payload only.
    if ann_consequence == 'insertion' and len(mutation) > len(reference):
        return _insertion_payload(reference, mutation) == _insertion_payload(ann_ref, ann_alt)

    # In-frame deletion-like rules are matched by deleted payload only.
    if ann_consequence == 'deletion' and len(reference) > len(mutation):
        return _deletion_payload(reference, mutation) == _deletion_payload(ann_ref, ann_alt)

    # SNP-like rules: compare resulting state (alt AA).
    if len(reference) == 1 and len(mutation) == 1:
        return ann_alt == mutation

    # Insertion-like rules: match by resulting AA state.
    if len(mutation) > len(reference):
        return ann_alt == mutation

    # Deletion-like rules: match by deleted reference block.
    if len(reference) > len(mutation):
        return ann_ref == reference

    # Fallback for rare same-length non-SNP AA rewrites.
    return ann_ref == reference and ann_alt == mutation


def _insertion_payload(reference: str, mutation: str) -> str:
    """Return insertion payload from an anchor+payload allele pair."""
    payload_len = len(mutation) - len(reference)
    if payload_len <= 0:
        return ''
    if mutation.startswith(reference):
        return mutation[len(reference):]
    return mutation[-payload_len:]


def _deletion_payload(reference: str, mutation: str) -> str:
    """Return deleted payload from an anchor+payload allele pair."""
    payload_len = len(reference) - len(mutation)
    if payload_len <= 0:
        return ''
    if reference.startswith(mutation):
        return reference[len(mutation):]
    return reference[-payload_len:]


def _is_frameshift_token(token: str) -> bool:
    """Return True for canonical or anchored frameshift tokens (``fsX`` / ``KfsX``)."""
    token_upper = token.upper()
    return token_upper == 'FSX' or (token_upper.endswith('FSX') and len(token_upper) == 4)


def _indel_anchor_mismatch_warning(
    *,
    rule_reference: str,
    rule_mutation: str,
    ann_ref: str,
    ann_alt: str,
    ann_consequence: str,
    gene_name: str,
    codon_pos: int,
) -> str | None:
    """Return a warning text when a matched-position indel has a different anchor AA."""
    if ann_consequence == 'insertion' and len(rule_mutation) > len(rule_reference):
        if ann_ref != rule_reference:
            return (
                f'Indel anchor mismatch at {gene_name}:{codon_pos + 1} (insertion): '
                f'rule anchor {rule_reference!r} vs observed anchor {ann_ref!r}. '
                'Matching by inserted payload only.'
            )
        return None

    if ann_consequence == 'deletion' and len(rule_reference) > len(rule_mutation):
        if ann_alt != rule_mutation:
            return (
                f'Indel anchor mismatch at {gene_name}:{codon_pos + 1} (deletion): '
                f'rule anchor {rule_mutation!r} vs observed anchor {ann_alt!r}. '
                'Matching by deleted payload only.'
            )
        return None

    return None


def _pick_matching_member_annotation(
    candidates: list[AnnotatedVariant],
    reference: str,
    mutation: str,
    gene_name: str,
    codon_pos: int,
    anchor_warning_cache: set[str],
) -> AnnotatedVariant | None:
    """Return one candidate annotation satisfying one combo-rule member."""
    if not candidates:
        return None

    for ann in candidates:
        anchor_warning = _indel_anchor_mismatch_warning(
            rule_reference=reference,
            rule_mutation=mutation,
            ann_ref=ann.ref_aa,
            ann_alt=ann.alt_aa,
            ann_consequence=ann.consequence,
            gene_name=gene_name,
            codon_pos=codon_pos,
        )
        if anchor_warning and anchor_warning not in anchor_warning_cache:
            logger.warning(anchor_warning)
            anchor_warning_cache.add(anchor_warning)

        if _matches_rule_alleles(
            reference=reference,
            mutation=mutation,
            ann_ref=ann.ref_aa,
            ann_alt=ann.alt_aa,
            ann_consequence=ann.consequence,
        ):
            return ann

    return None


