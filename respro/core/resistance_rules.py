"""
Resistance rule matching against annotated variants.
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

# Canonical wildcard token used in stored rules.
_WILDCARD_MUTATIONS: frozenset[str] = frozenset({'any'})


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

    rules = [
        ResistanceRule(
            id=r['id'],
            gene_name=r['gene_name'],
            gene_id=r['gene_id'],
            drug_name=r['drug_name'],
            drug_id=r['drug_id'],
            reference_identifier=r['reference_identifier'] or '',
            position=r['position'],
            reference=r['reference'] or '',
            mutation=r['mutation'],
            phenotype=r['phenotype'],
            clinical_phenotype=r['clinical_phenotype'] or 'unknown',
            ic50=r['ic50'] or '',
            fold_ic50=r['fold_ic50'] or '',
            source=r['source'] or '',
            comment=r['comment'] or '',
            pubchem_url=r['pubchem_url'] or '',
            description=r['description'] or '',
        )
        for r in rows
    ]

    if rules:
        _attach_publications_to_rules(conn, rules)

    logger.info('Loaded %d resistance rule(s)', len(rules))
    return rules


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
    placeholders = ','.join('?' * len(rule_ids))
    pub_rows = conn.execute(
        f'SELECT rp.rule_id, p.id, p.doi, p.title, p.pubmed_id, p.raw_input '
        f'FROM rule_publication rp '
        f'JOIN publication p ON p.id = rp.publication_id '
        f'WHERE rp.rule_id IN ({placeholders})',
        rule_ids,
    ).fetchall()

    pubs_by_rule: dict[int, list[Publication]] = {}
    for row in pub_rows:
        pubs_by_rule.setdefault(int(row['rule_id']), []).append(
            Publication(
                id=int(row['id']),
                doi=row['doi'] or '',
                title=row['title'] or '',
                pubmed_id=row['pubmed_id'] or '',
                raw_input=row['raw_input'] or '',
            )
        )
    for rule in rules:
        rule.publications = pubs_by_rule.get(rule.id, [])


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
    # Build a lookup: (gene_name, position, mutation) -> list[ResistanceRule]
    rule_index: dict[tuple[str, int, str], list[ResistanceRule]] = {}
    for rule in rules:
        key = (rule.gene_name, rule.position, rule.mutation)
        rule_index.setdefault(key, []).append(rule)

    # Index rules with wildcard mutation
    wildcard_index: dict[tuple[str, int], list[ResistanceRule]] = {}
    for rule in rules:
        if rule.mutation.lower() in _WILDCARD_MUTATIONS:
            wildcard_index.setdefault((rule.gene_name, rule.position), []).append(rule)

    hit_count = 0
    for ann in annotations:
        if not ann.gene_name or not ann.alt_aa or ann.consequence == 'synonymous':
            continue

        # Exact match
        key = (ann.gene_name, ann.codon_pos, ann.alt_aa)
        if key in rule_index:
            ann.rule_matches.extend(rule_index[key])
            hit_count += len(rule_index[key])

        # Wildcard match (any non-reference AA at this position)
        wkey = (ann.gene_name, ann.codon_pos)
        if wkey in wildcard_index and ann.alt_aa != ann.ref_aa:
            ann.rule_matches.extend(wildcard_index[wkey])
            hit_count += len(wildcard_index[wkey])

    logger.info('Matched %d rule hit(s) across %d annotation(s)', hit_count, len(annotations))
    return annotations


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

    rule_sets: dict[int, ResistanceRuleSet] = {}
    for r in set_rows:
        rule_sets[r['id']] = ResistanceRuleSet(
            id=r['id'],
            drug_name=r['drug_name'],
            drug_id=r['drug_id'],
            phenotype=r['phenotype'],
            clinical_phenotype=r['clinical_phenotype'] or 'unknown',
            ic50=r['ic50'] or '',
            fold_ic50=r['fold_ic50'] or '',
            source=r['source'] or '',
            group_name=r['group_name'] or '',
            comment=r['comment'] or '',
            pubchem_url=r['pubchem_url'] or '',
            description=r['description'] or '',
        )

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
    placeholders = ','.join('?' * len(set_ids))
    pub_rows = conn.execute(
        f'SELECT rsp.rule_set_id, p.id, p.doi, p.title, p.pubmed_id, p.raw_input '
        f'FROM rule_set_publication rsp '
        f'JOIN publication p ON p.id = rsp.publication_id '
        f'WHERE rsp.rule_set_id IN ({placeholders})',
        set_ids,
    ).fetchall()

    pubs_by_set: dict[int, list[Publication]] = {}
    for row in pub_rows:
        pubs_by_set.setdefault(int(row['rule_set_id']), []).append(
            Publication(
                id=int(row['id']),
                doi=row['doi'] or '',
                title=row['title'] or '',
                pubmed_id=row['pubmed_id'] or '',
                raw_input=row['raw_input'] or '',
            )
        )
    for rs in rule_sets:
        rs.publications = pubs_by_set.get(rs.id, [])


def match_rule_sets(
    annotations: list[AnnotatedVariant],
    rule_sets: list[ResistanceRuleSet],
) -> list[ComboRuleHit]:
    """
    Match annotated variants against combination resistance rule sets.

    A rule set fires only when every member mutation is present in the annotated
    variant list. Wildcard (``any``) members match any non-reference amino acid
    at the given position.

    :param annotations: list of annotated variants
    :param rule_sets: list of ResistanceRuleSet objects with populated members
    :return: list of ComboRuleHit for every rule set that fired
    """
    if not rule_sets:
        return []

    # Build lookup from (gene_name, codon_pos, mutation_token) -> AnnotatedVariant.
    # Synonymous variants cannot satisfy a resistance rule member.
    present: dict[tuple[str, int, str], AnnotatedVariant] = {}
    wildcard_present: dict[tuple[str, int], AnnotatedVariant] = {}
    for ann in annotations:
        if not ann.gene_name or not ann.alt_aa or ann.consequence == 'synonymous':
            continue
        present[(ann.gene_name, ann.codon_pos, ann.alt_aa)] = ann
        if ann.alt_aa != ann.ref_aa:
            wildcard_present.setdefault((ann.gene_name, ann.codon_pos), ann)

    hits: list[ComboRuleHit] = []
    for rule_set in rule_sets:
        contributing: list[AnnotatedVariant] = []
        all_matched = True
        for member in rule_set.members:
            if member.mutation.lower() in _WILDCARD_MUTATIONS:
                ann = wildcard_present.get((member.gene_name, member.position))
            else:
                ann = present.get((member.gene_name, member.position, member.mutation))
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

