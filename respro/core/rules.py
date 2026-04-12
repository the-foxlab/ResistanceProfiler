"""
Resistance rule matching — load rules from the project database and match against annotated variants.
"""

from __future__ import annotations

import logging
import sqlite3

from respro.db.models import (
    AnnotatedVariant,
    ComboRuleHit,
    Publication,
    ResistanceRule,
    ResistanceRuleSet,
    ResistanceRuleSetMember,
)

logger = logging.getLogger(__name__)


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
    for ann in annotations:
        if not ann.gene_name or not ann.alt_aa or ann.consequence == 'synonymous':
            continue

        key = (ann.gene_name, ann.codon_pos)
        candidates = rule_index.get(key, [])
        for rule in candidates:
            if _matches_rule_alleles(
                reference=rule.reference,
                mutation=rule.mutation,
                ann_ref=ann.ref_aa,
                ann_alt=ann.alt_aa,
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
    for rule_set in rule_sets:
        contributing: list[AnnotatedVariant] = []
        all_matched = True
        for member in rule_set.members:
            ann = _pick_matching_member_annotation(
                present.get((member.gene_name, member.position), []),
                member.reference,
                member.mutation,
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
) -> bool:
    """Compare one rule allele pair with one annotation allele pair."""
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


def _pick_matching_member_annotation(
    candidates: list[AnnotatedVariant],
    reference: str,
    mutation: str,
) -> AnnotatedVariant | None:
    """Return one candidate annotation satisfying one combo-rule member."""
    if not candidates:
        return None

    return next(
        (
            ann
            for ann in candidates
            if _matches_rule_alleles(
                reference=reference,
                mutation=mutation,
                ann_ref=ann.ref_aa,
                ann_alt=ann.alt_aa,
            )
        ),
        None,
    )


