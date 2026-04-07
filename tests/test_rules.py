"""
Tests for resistance rule loading and matching.
"""

import sqlite3
from pathlib import Path

import pytest

from respro.core.resistance_rules import load_rules, load_rule_sets, match_rules, match_rule_sets
from respro.db.models import (
    AnnotatedVariant, ResistanceRule, ResistanceRuleSet, ResistanceRuleSetMember, VariantCall,
)
from respro.db.schema import open_project_db


class TestLoadRules:
    def test_loads_from_db(self, project_db: Path):
        conn = open_project_db(project_db)
        rules = load_rules(conn, reference_id=1)
        conn.close()

        assert len(rules) == 1
        rule = rules[0]
        assert rule.gene_name == 'gag'
        assert rule.drug_name == 'TestDrug'
        assert rule.position == 1  # 0-based stored in DB (was 2 in 1-based TSV)
        assert rule.reference == 'K'
        assert rule.mutation == 'E'
        assert rule.phenotype == 'resistant'
        assert rule.reference_identifier == ''
        assert rule.ic50 == ''
        assert rule.publications == []


class TestMatchRules:
    def test_exact_match(self):
        rule = ResistanceRule(
            id=1, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1,
            reference_identifier='',
            position=1, reference='K', mutation='E',  # 0-based
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            gene_name='gag', codon_pos=1,  # 0-based
            ref_aa='K', alt_aa='E', consequence='missense',
        )
        result = match_rules([ann], [rule])
        assert len(result) == 1
        assert result[0].is_resistance_hit
        assert result[0].rule_matches[0].drug_name == 'DrugA'

    def test_no_match_wrong_position(self):
        rule = ResistanceRule(
            id=1, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1,
            reference_identifier='',
            position=4, reference='K', mutation='E',  # 0-based, different pos
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            gene_name='gag', codon_pos=1,  # 0-based
            ref_aa='K', alt_aa='E', consequence='missense',
        )
        result = match_rules([ann], [rule])
        assert not result[0].is_resistance_hit

    def test_no_match_synonymous(self):
        rule = ResistanceRule(
            id=1, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1,
            reference_identifier='',
            position=1, reference='K', mutation='K',  # 0-based
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            gene_name='gag', codon_pos=1,  # 0-based
            ref_aa='K', alt_aa='K', consequence='synonymous',
        )
        result = match_rules([ann], [rule])
        assert not result[0].is_resistance_hit

    def test_wildcard_match(self):
        rule = ResistanceRule(
            id=1, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1,
            reference_identifier='',
            position=1, reference='K', mutation='any',  # 0-based
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            gene_name='gag', codon_pos=1,  # 0-based
            ref_aa='K', alt_aa='Q', consequence='missense',
        )
        result = match_rules([ann], [rule])
        assert result[0].is_resistance_hit

    def test_stop_rule_star_is_not_wildcard(self):
        rule = ResistanceRule(
            id=1, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1,
            reference_identifier='',
            position=1, reference='K', mutation='*',
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            gene_name='gag', codon_pos=1,
            ref_aa='K', alt_aa='Q', consequence='missense',
        )
        result = match_rules([ann], [rule])
        assert not result[0].is_resistance_hit

    def test_x_is_not_wildcard_in_stored_rules(self):
        rule = ResistanceRule(
            id=1, gene_name='gag', gene_id=1,
            drug_name='DrugA', drug_id=1,
            reference_identifier='',
            position=1, reference='K', mutation='x',
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            gene_name='gag', codon_pos=1,
            ref_aa='K', alt_aa='Q', consequence='missense',
        )
        result = match_rules([ann], [rule])
        assert not result[0].is_resistance_hit

    def test_multiple_drugs_same_position(self):
        rules = [
            ResistanceRule(
                id=1, gene_name='gag', gene_id=1,
                drug_name='DrugA', drug_id=1,
                reference_identifier='',
                position=1, reference='K', mutation='E',  # 0-based
                phenotype='resistant',
            ),
            ResistanceRule(
                id=2, gene_name='gag', gene_id=1,
                drug_name='DrugB', drug_id=2,
                reference_identifier='',
                position=1, reference='K', mutation='E',  # 0-based
                phenotype='intermediate',
            ),
        ]
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            gene_name='gag', codon_pos=1,  # 0-based
            ref_aa='K', alt_aa='E', consequence='missense',
        )
        result = match_rules([ann], rules)
        assert len(result[0].rule_matches) == 2
        drug_names = {m.drug_name for m in result[0].rule_matches}
        assert drug_names == {'DrugA', 'DrugB'}


# ─── Helpers for combo rule tests ────────────────────────────────────────────

def _make_ann(gene: str, codon_pos: int, ref_aa: str, alt_aa: str) -> AnnotatedVariant:
    return AnnotatedVariant(
        variant=VariantCall(chrom='ref', pos=codon_pos * 3, ref='A', alt='T',
                            allele_freq=0.9, depth=100),
        gene_name=gene,
        codon_pos=codon_pos,
        ref_aa=ref_aa,
        alt_aa=alt_aa,
        consequence='missense',
    )


def _make_rule_set(
    members: list[tuple[str, int, str]],  # (gene_name, position_0based, mutation)
) -> ResistanceRuleSet:
    rs = ResistanceRuleSet(
        id=1, drug_name='DrugA', drug_id=1,
        phenotype='resistant',
    )
    for order, (gene, pos, mut) in enumerate(members):
        rs.members.append(ResistanceRuleSetMember(
            id=order + 1, rule_set_id=1,
            gene_name=gene, gene_id=1,
            reference_identifier='',
            position=pos, reference='', mutation=mut,
        ))
    return rs


class TestMatchRuleSets:
    def test_all_of_fires_when_all_members_present(self):
        rule_set = _make_rule_set([('gag', 1, 'E'), ('gag', 5, 'V')])
        annotations = [
            _make_ann('gag', 1, 'K', 'E'),
            _make_ann('gag', 5, 'A', 'V'),
        ]
        hits = match_rule_sets(annotations, [rule_set])
        assert len(hits) == 1
        assert hits[0].rule_set.drug_name == 'DrugA'
        assert len(hits[0].matched_variants) == 2

    def test_all_of_does_not_fire_when_one_member_missing(self):
        rule_set = _make_rule_set([('gag', 1, 'E'), ('gag', 5, 'V')])
        annotations = [_make_ann('gag', 1, 'K', 'E')]  # only one of two
        hits = match_rule_sets(annotations, [rule_set])
        assert hits == []

    def test_all_of_does_not_fire_when_all_absent(self):
        rule_set = _make_rule_set([('gag', 1, 'E'), ('gag', 5, 'V')])
        hits = match_rule_sets([], [rule_set])
        assert hits == []

    def test_synonymous_variant_does_not_satisfy_member(self):
        rule_set = _make_rule_set([('gag', 1, 'E'), ('gag', 5, 'V')])
        syn = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='T', allele_freq=0.9, depth=100),
            gene_name='gag', codon_pos=1,
            ref_aa='K', alt_aa='K', consequence='synonymous',
        )
        present = _make_ann('gag', 5, 'A', 'V')
        hits = match_rule_sets([syn, present], [rule_set])
        assert hits == []

    def test_wildcard_member_matches_any_non_ref_aa(self):
        rule_set = _make_rule_set([('gag', 1, 'any'), ('gag', 5, 'V')])
        annotations = [
            _make_ann('gag', 1, 'K', 'Q'),  # any non-ref matches
            _make_ann('gag', 5, 'A', 'V'),
        ]
        hits = match_rule_sets(annotations, [rule_set])
        assert len(hits) == 1

    def test_empty_rule_sets_returns_empty(self):
        ann = _make_ann('gag', 1, 'K', 'E')
        hits = match_rule_sets([ann], [])
        assert hits == []

    def test_multiple_rule_sets_both_fire(self):
        rs1 = _make_rule_set([('gag', 1, 'E'), ('gag', 5, 'V')])
        rs2 = _make_rule_set([('gag', 2, 'T'), ('gag', 8, 'I')])
        rs2.id = 2
        annotations = [
            _make_ann('gag', 1, 'K', 'E'),
            _make_ann('gag', 5, 'A', 'V'),
            _make_ann('gag', 2, 'S', 'T'),
            _make_ann('gag', 8, 'L', 'I'),
        ]
        hits = match_rule_sets(annotations, [rs1, rs2])
        assert len(hits) == 2

    def test_to_dict_is_serializable(self):
        rule_set = _make_rule_set([('gag', 1, 'E'), ('gag', 5, 'V')])
        annotations = [_make_ann('gag', 1, 'K', 'E'), _make_ann('gag', 5, 'A', 'V')]
        hits = match_rule_sets(annotations, [rule_set])
        d = hits[0].to_dict()
        assert d['drug'] == 'DrugA'
        assert len(d['members']) == 2
        assert len(d['matched_variants']) == 2
        # positions in output are 1-based
        assert d['members'][0]['position'] == 2
        assert d['members'][1]['position'] == 6


class TestLoadRuleSets:
    def test_loads_combo_rules_from_db(self, project_db_with_combo: Path) -> None:
        conn = open_project_db(project_db_with_combo)
        rule_sets = load_rule_sets(conn, reference_id=1)
        conn.close()

        assert len(rule_sets) == 1
        rs = rule_sets[0]
        assert rs.drug_name == 'TestDrug'
        assert rs.phenotype == 'resistant'
        assert rs.group_name == 'combo_group_1'
        assert len(rs.members) == 2
        positions = {m.position for m in rs.members}
        assert positions == {1, 5}

    def test_returns_empty_when_no_combo_rules(self, project_db: Path) -> None:
        conn = open_project_db(project_db)
        rule_sets = load_rule_sets(conn, reference_id=1)
        conn.close()
        assert rule_sets == []


@pytest.fixture()
def project_db_with_combo(project_db: Path) -> Path:
    """Extend the minimal project_db fixture with a two-member combo rule set."""
    conn = sqlite3.connect(str(project_db))
    conn.execute('PRAGMA foreign_keys=ON')
    # The gene_id=1 ('gag') and drug_id=1 ('TestDrug') already exist from project_db.
    conn.execute(
        'INSERT INTO resistance_rule_set (drug_id, phenotype, group_name) VALUES (?, ?, ?)',
        (1, 'resistant', 'combo_group_1'),
    )
    rule_set_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    conn.executemany(
        'INSERT INTO resistance_rule_set_member '
        '(rule_set_id, gene_id, reference_identifier, position, reference, mutation) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        [
            (rule_set_id, 1, '', 1, 'K', 'E'),
            (rule_set_id, 1, '', 5, 'A', 'V'),
        ],
    )
    conn.commit()
    conn.close()
    return project_db


