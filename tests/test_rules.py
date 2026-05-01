"""
Tests for resistance rule loading and matching.
"""

import textwrap
from pathlib import Path

from conftest import TINY_REF_SEQ, write_genbank
from typer.testing import CliRunner

from respro.cli.init import init_project
from respro.cli.main import app
from respro.core.rules import (
    FormulaRuleRuntime,
    load_rules,
    match_formula_rules,
    match_rule_sets,
    match_rules,
)
from respro.db.models import (
    AnnotatedVariant,
    ResistanceRule,
    ResistanceRuleSet,
    ResistanceRuleSetMember,
    VariantCall,
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

    def test_any_token_is_not_matched_as_wildcard(self):
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
        assert not result[0].is_resistance_hit

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

    def test_deletion_rule_requires_full_reference_block(self):
        rules = [
            ResistanceRule(
                id=1,
                gene_name='gag',
                gene_id=1,
                drug_name='DrugA',
                drug_id=1,
                reference_identifier='',
                position=4,
                reference='YP',
                mutation='Y',
                phenotype='resistant',
            ),
            ResistanceRule(
                id=2,
                gene_name='gag',
                gene_id=1,
                drug_name='DrugB',
                drug_id=2,
                reference_identifier='',
                position=4,
                reference='YQ',
                mutation='Y',
                phenotype='resistant',
            ),
        ]
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=12, ref='A', alt='T', allele_freq=0.9, depth=100),
            gene_name='gag',
            codon_pos=4,
            ref_aa='YP',
            alt_aa='Y',
            consequence='deletion',
        )

        result = match_rules([ann], rules)

        assert len(result[0].rule_matches) == 1
        assert result[0].rule_matches[0].drug_name == 'DrugA'

    def test_insertion_rule_matches_by_mutation_state(self):
        rules = [
            ResistanceRule(
                id=1,
                gene_name='gag',
                gene_id=1,
                drug_name='DrugA',
                drug_id=1,
                reference_identifier='',
                position=4,
                reference='A',
                mutation='FG',
                phenotype='resistant',
            )
        ]
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=12, ref='A', alt='T', allele_freq=0.9, depth=100),
            gene_name='gag',
            codon_pos=4,
            ref_aa='F',
            alt_aa='FG',
            consequence='insertion',
        )

        result = match_rules([ann], rules)

        assert len(result[0].rule_matches) == 1
        assert result[0].rule_matches[0].drug_name == 'DrugA'

    def test_insertion_rule_matches_by_payload_when_anchor_differs(self, caplog):
        caplog.set_level('WARNING')
        rule = ResistanceRule(
            id=1,
            gene_name='gag',
            gene_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='',
            position=4,
            reference='A',
            mutation='AG',
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=12, ref='A', alt='T', allele_freq=0.9, depth=100),
            gene_name='gag',
            codon_pos=4,
            ref_aa='R',
            alt_aa='RG',
            consequence='insertion',
        )

        result = match_rules([ann], [rule])

        assert len(result[0].rule_matches) == 1
        assert 'Indel anchor mismatch' in caplog.text

    def test_deletion_rule_matches_by_payload_when_anchor_differs(self, caplog):
        caplog.set_level('WARNING')
        rule = ResistanceRule(
            id=1,
            gene_name='gag',
            gene_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='',
            position=4,
            reference='AG',
            mutation='A',
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=12, ref='A', alt='T', allele_freq=0.9, depth=100),
            gene_name='gag',
            codon_pos=4,
            ref_aa='RG',
            alt_aa='R',
            consequence='deletion',
        )

        result = match_rules([ann], [rule])

        assert len(result[0].rule_matches) == 1
        assert 'Indel anchor mismatch' in caplog.text

    def test_frameshift_rule_matches_when_anchor_differs(self):
        rule = ResistanceRule(
            id=1,
            gene_name='gag',
            gene_id=1,
            drug_name='DrugFs',
            drug_id=1,
            reference_identifier='',
            position=4,
            reference='K',
            mutation='KfsX',
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=12, ref='AA', alt='A', allele_freq=0.9, depth=100),
            gene_name='gag',
            codon_pos=4,
            ref_aa='R',
            alt_aa='RfsX',
            consequence='frameshift',
        )

        result = match_rules([ann], [rule])
        assert len(result[0].rule_matches) == 1

    def test_frameshift_rule_does_not_match_non_frameshift(self):
        rule = ResistanceRule(
            id=1,
            gene_name='gag',
            gene_id=1,
            drug_name='DrugFs',
            drug_id=1,
            reference_identifier='',
            position=4,
            reference='K',
            mutation='KfsX',
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=12, ref='A', alt='G', allele_freq=0.9, depth=100),
            gene_name='gag',
            codon_pos=4,
            ref_aa='K',
            alt_aa='E',
            consequence='missense',
        )

        result = match_rules([ann], [rule])
        assert len(result[0].rule_matches) == 0


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

    def test_low_af_member_does_not_satisfy_combo_rule(self):
        rule_set = _make_rule_set([('gag', 1, 'E'), ('gag', 5, 'V')])
        low_af = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='T', allele_freq=0.5, depth=100),
            gene_name='gag', codon_pos=1,
            ref_aa='K', alt_aa='E', consequence='missense',
        )
        high_af = _make_ann('gag', 5, 'A', 'V')

        hits = match_rule_sets([low_af, high_af], [rule_set])
        assert hits == []

    def test_member_at_exact_af_threshold_does_not_satisfy_combo_rule(self):
        rule_set = _make_rule_set([('gag', 1, 'E'), ('gag', 5, 'V')])
        at_threshold = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='T', allele_freq=0.75, depth=100),
            gene_name='gag', codon_pos=1,
            ref_aa='K', alt_aa='E', consequence='missense',
        )
        high_af = _make_ann('gag', 5, 'A', 'V')

        hits = match_rule_sets([at_threshold, high_af], [rule_set])
        assert hits == []

    def test_any_member_does_not_match_combo_rule(self):
        rule_set = _make_rule_set([('gag', 1, 'any'), ('gag', 5, 'V')])
        annotations = [
            _make_ann('gag', 1, 'K', 'Q'),
            _make_ann('gag', 5, 'A', 'V'),
        ]
        hits = match_rule_sets(annotations, [rule_set])
        assert hits == []

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

    def test_shared_member_all_members_present_both_rule_sets_fire(self):
        # Shared member: gag K2E (0-based codon_pos=1) is present in both sets.
        rs1 = _make_rule_set([('gag', 1, 'E'), ('gag', 5, 'V')])
        rs1.id = 1
        rs2 = _make_rule_set([('gag', 1, 'E'), ('gag', 8, 'I')])
        rs2.id = 2

        shared = _make_ann('gag', 1, 'K', 'E')
        unique_rs1 = _make_ann('gag', 5, 'A', 'V')
        unique_rs2 = _make_ann('gag', 8, 'L', 'I')
        annotations = [shared, unique_rs1, unique_rs2]

        hits = match_rule_sets(annotations, [rs1, rs2])

        assert len(hits) == 2
        hit_by_id = {hit.rule_set.id: hit for hit in hits}
        assert 1 in hit_by_id
        assert 2 in hit_by_id
        assert shared in hit_by_id[1].matched_variants
        assert shared in hit_by_id[2].matched_variants

    def test_shared_member_only_fully_satisfied_rule_set_fires(self):
        # Shared member present, but rs2 unique member is missing.
        rs1 = _make_rule_set([('gag', 1, 'E'), ('gag', 5, 'V')])
        rs1.id = 1
        rs2 = _make_rule_set([('gag', 1, 'E'), ('gag', 8, 'I')])
        rs2.id = 2

        shared = _make_ann('gag', 1, 'K', 'E')
        unique_rs1 = _make_ann('gag', 5, 'A', 'V')
        annotations = [shared, unique_rs1]

        hits = match_rule_sets(annotations, [rs1, rs2])

        assert len(hits) == 1
        assert hits[0].rule_set.id == 1
        assert shared in hits[0].matched_variants
        assert unique_rs1 in hits[0].matched_variants

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

    def test_insertion_member_matches_by_mutation_state(self):
        rs = ResistanceRuleSet(id=1, drug_name='DrugA', drug_id=1, phenotype='resistant')
        rs.members.append(
            ResistanceRuleSetMember(
                id=1,
                rule_set_id=1,
                gene_name='gag',
                gene_id=1,
                reference_identifier='',
                position=4,
                reference='A',
                mutation='FG',
            )
        )
        rs.members.append(
            ResistanceRuleSetMember(
                id=2,
                rule_set_id=1,
                gene_name='gag',
                gene_id=1,
                reference_identifier='',
                position=6,
                reference='P',
                mutation='V',
            )
        )

        annotations = [
            AnnotatedVariant(
                variant=VariantCall(chrom='ref', pos=12, ref='A', alt='T', allele_freq=0.9, depth=100),
                gene_name='gag',
                codon_pos=4,
                ref_aa='F',
                alt_aa='FG',
                consequence='insertion',
            ),
            _make_ann('gag', 6, 'P', 'V'),
        ]

        hits = match_rule_sets(annotations, [rs])
        assert len(hits) == 1


class TestMatchFormulaRules:
    def _atomic_rule(self, external_id: str, mutation: str, *, position: int = 1) -> ResistanceRule:
        return ResistanceRule(
            id=1,
            gene_name='gag',
            gene_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='',
            position=position,
            reference='K',
            mutation=mutation,
            phenotype='resistant',
            external_id=external_id,
        )

    def _formula(self, expression: str, members: list[ResistanceRule]) -> FormulaRuleRuntime:
        return FormulaRuleRuntime(
            id=1,
            formula_id='formula_1',
            label='Formula 1',
            normalized_expression=expression,
            drug_name='DrugA',
            drug_id=1,
            phenotype='resistant',
            clinical_phenotype='unknown',
            ic50='',
            fold_ic50='',
            score='',
            source='',
            comment='',
            member_rules={rule.external_id: rule for rule in members if rule.external_id},
        )

    def test_formula_and_matches_when_all_members_pass_af_gate(self) -> None:
        mut_a = self._atomic_rule('mut_a', 'E', position=1)
        mut_b = self._atomic_rule('mut_b', 'V', position=5)
        formula = self._formula('(mut_a AND mut_b)', [mut_a, mut_b])

        ann_a = _make_ann('gag', 1, 'K', 'E')
        ann_b = _make_ann('gag', 5, 'A', 'V')
        ann_a.rule_matches = [mut_a]
        ann_b.rule_matches = [mut_b]

        hits = match_formula_rules([ann_a, ann_b], [formula], member_af_threshold=0.75)

        assert len(hits) == 1
        assert hits[0].rule_set.group_name == 'Formula 1'
        assert {v.alt_aa for v in hits[0].matched_variants} == {'E', 'V'}

    def test_formula_not_uses_af_gated_presence(self) -> None:
        mut_a = self._atomic_rule('mut_a', 'E', position=1)
        mut_b = self._atomic_rule('mut_b', 'V', position=5)
        formula = self._formula('(mut_a AND (NOT mut_b))', [mut_a, mut_b])

        ann_a = _make_ann('gag', 1, 'K', 'E')
        ann_a.rule_matches = [mut_a]
        ann_b = _make_ann('gag', 5, 'A', 'V')
        ann_b.variant.allele_freq = 0.60
        ann_b.rule_matches = [mut_b]

        hits = match_formula_rules([ann_a, ann_b], [formula], member_af_threshold=0.75)

        assert len(hits) == 1
        assert {v.alt_aa for v in hits[0].matched_variants} == {'E'}

    def test_formula_or_prefers_highest_af_branch(self) -> None:
        mut_a = self._atomic_rule('mut_a', 'E', position=1)
        mut_b = self._atomic_rule('mut_b', 'V', position=5)
        formula = self._formula('(mut_a OR mut_b)', [mut_a, mut_b])

        ann_a = _make_ann('gag', 1, 'K', 'E')
        ann_a.variant.allele_freq = 0.80
        ann_a.rule_matches = [mut_a]
        ann_b = _make_ann('gag', 5, 'A', 'V')
        ann_b.variant.allele_freq = 0.95
        ann_b.rule_matches = [mut_b]

        hits = match_formula_rules([ann_a, ann_b], [formula], member_af_threshold=0.75)

        assert len(hits) == 1
        assert len(hits[0].matched_variants) == 1
        assert hits[0].matched_variants[0].alt_aa == 'V'


class TestInitAddValidate:
    def test_add_validate_checks_rules_without_writing(self, tmp_path: Path) -> None:
        genbank_path = tmp_path / 'tiny.gb'
        write_genbank(
            genbank_path,
            [
                {
                    'id': 'tiny_ref',
                    'accession': 'tiny_ref',
                    'sequence': TINY_REF_SEQ,
                    'genes': [{'gene': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
                }
            ],
        )

        base_rules = tmp_path / 'base.tsv'
        base_rules.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tE\tDrugA\tresistant
        """))

        project_db = tmp_path / 'project.db'
        init_project(
            db_path=project_db,
            name='test',
            genbank_paths=[genbank_path],
            rules_tsv=base_rules,
            additional_info=False,
        )

        add_rules = tmp_path / 'add.tsv'
        add_rules.write_text(textwrap.dedent("""\
            gene\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t6\tP\tV\tDrugA\tresistant
        """))

        result = CliRunner().invoke(
            app,
            [
                'add',
                '--project', str(project_db),
                '--rules', str(add_rules),
                '--validate',
                '--no-additional-info',
            ],
        )
        assert result.exit_code == 0, result.output
        assert 'Rules validation passed' in result.output

        conn = open_project_db(project_db)
        count = conn.execute('SELECT COUNT(*) FROM resistance_rule').fetchone()[0]
        conn.close()
        assert count == 1


