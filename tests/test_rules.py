"""
Tests for resistance rule loading and matching.
"""

import textwrap
from pathlib import Path

import pytest
from conftest import TINY_REF_SEQ, write_genbank
from typer.testing import CliRunner

from respro.cli.init import init_project
from respro.cli.main import app
from respro.core.rules import (
    match_formula_rules,
    match_rules,
)
from respro.db.models import (
    AnnotatedVariant,
    CodonState,
    FormulaRuleRuntime,
    ResistanceRule,
    VariantCall,
)
from respro.db.rules_queries import load_rules
from respro.db.schema import open_project_db


class TestLoadRules:
    def test_loads_from_db(self, project_db: Path):
        conn = open_project_db(project_db)
        rules = load_rules(conn, reference_id=1)
        conn.close()

        assert len(rules) == 1
        rule = rules[0]
        assert rule.feature_name == 'gag'
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
            id=1, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1,
            reference_identifier='',
            position=1, reference='K', mutation='E',  # 0-based
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            feature_name='gag', codon_pos=1,  # 0-based
            ref_aa='K', alt_aa='E', consequence='missense',
        )
        result = match_rules([ann], [rule])
        assert len(result) == 1
        assert result[0].is_resistance_hit
        assert result[0].rule_matches[0].drug_name == 'DrugA'

    def test_no_match_wrong_position(self):
        rule = ResistanceRule(
            id=1, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1,
            reference_identifier='',
            position=4, reference='K', mutation='E',  # 0-based, different pos
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            feature_name='gag', codon_pos=1,  # 0-based
            ref_aa='K', alt_aa='E', consequence='missense',
        )
        result = match_rules([ann], [rule])
        assert not result[0].is_resistance_hit

    def test_no_match_synonymous(self):
        rule = ResistanceRule(
            id=1, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1,
            reference_identifier='',
            position=1, reference='K', mutation='K',  # 0-based
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            feature_name='gag', codon_pos=1,  # 0-based
            ref_aa='K', alt_aa='K', consequence='synonymous',
        )
        result = match_rules([ann], [rule])
        assert not result[0].is_resistance_hit

    def test_any_token_is_not_matched_as_wildcard(self):
        rule = ResistanceRule(
            id=1, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1,
            reference_identifier='',
            position=1, reference='K', mutation='any',  # 0-based
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            feature_name='gag', codon_pos=1,  # 0-based
            ref_aa='K', alt_aa='Q', consequence='missense',
        )
        result = match_rules([ann], [rule])
        assert not result[0].is_resistance_hit

    def test_stop_rule_star_is_not_wildcard(self):
        rule = ResistanceRule(
            id=1, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1,
            reference_identifier='',
            position=1, reference='K', mutation='*',
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            feature_name='gag', codon_pos=1,
            ref_aa='K', alt_aa='Q', consequence='missense',
        )
        result = match_rules([ann], [rule])
        assert not result[0].is_resistance_hit

    def test_x_is_not_wildcard_in_stored_rules(self):
        rule = ResistanceRule(
            id=1, feature_name='gag', feature_id=1,
            drug_name='DrugA', drug_id=1,
            reference_identifier='',
            position=1, reference='K', mutation='x',
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            feature_name='gag', codon_pos=1,
            ref_aa='K', alt_aa='Q', consequence='missense',
        )
        result = match_rules([ann], [rule])
        assert not result[0].is_resistance_hit

    def test_multiple_drugs_same_position(self):
        rules = [
            ResistanceRule(
                id=1, feature_name='gag', feature_id=1,
                drug_name='DrugA', drug_id=1,
                reference_identifier='',
                position=1, reference='K', mutation='E',  # 0-based
                phenotype='resistant',
            ),
            ResistanceRule(
                id=2, feature_name='gag', feature_id=1,
                drug_name='DrugB', drug_id=2,
                reference_identifier='',
                position=1, reference='K', mutation='E',  # 0-based
                phenotype='intermediate',
            ),
        ]
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=4, ref='A', alt='G', allele_freq=0.9, depth=100),
            feature_name='gag', codon_pos=1,  # 0-based
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
                feature_name='gag',
                feature_id=1,
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
                feature_name='gag',
                feature_id=1,
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
            feature_name='gag',
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
                feature_name='gag',
                feature_id=1,
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
            feature_name='gag',
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
            feature_name='gag',
            feature_id=1,
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
            feature_name='gag',
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
            feature_name='gag',
            feature_id=1,
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
            feature_name='gag',
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
            feature_name='gag',
            feature_id=1,
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
            feature_name='gag',
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
            feature_name='gag',
            feature_id=1,
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
            feature_name='gag',
            codon_pos=4,
            ref_aa='K',
            alt_aa='E',
            consequence='missense',
        )

        result = match_rules([ann], [rule])
        assert len(result[0].rule_matches) == 0


class TestMatchCombinedStates:
    """Rule matching checks both the single-exchange alt_aa and every combined_states alt_aa."""

    @staticmethod
    def _rule(rule_id: int, mutation: str, *, position: int = 1) -> ResistanceRule:
        return ResistanceRule(
            id=rule_id, feature_name='testf', feature_id=1,
            drug_name='DrugA', drug_id=1, reference_identifier='',
            position=position, reference='K', mutation=mutation,
            phenotype='resistant',
        )

    @staticmethod
    def _ann(alt_aa: str, allele_freq: float, combined_states: list[CodonState] | None = None,
             codon_pos: int = 1) -> AnnotatedVariant:
        return AnnotatedVariant(
            variant=VariantCall(chrom='c', pos=4, ref='A', alt='T', allele_freq=allele_freq, depth=100),
            feature_name='testf', codon_pos=codon_pos,
            ref_aa='K', alt_aa=alt_aa, consequence='missense',
            combined_states=combined_states or [],
        )

    def test_rule_fires_via_single_on_non_combined_annotation(self) -> None:
        """A single-SNP K20N annotation still matches a K20N rule as today."""
        rule = self._rule(1, 'N')
        ann = self._ann('N', 0.9)
        result = match_rules([ann], [rule])
        assert len(result[0].rule_matches) == 1
        assert result[0].rule_matches[0].id == 1
        # Single hit -> effect lower is the row's own AF.
        assert result[0].rule_effect_aa_freq[1] == pytest.approx(0.9)

    def test_rule_fires_via_combined_state_not_single(self) -> None:
        """A->T row (single=M, combined_states=[I@0.5, M@0.5]): K20I fires via combined only."""
        rule_i = self._rule(10, 'I')
        states = [
            CodonState(alt_codon='ATT', alt_aa='I', lower=0.5, upper=0.5,
                       forced_fraction=1.0, accepted=True, member_indices=(0, 1)),
            CodonState(alt_codon='ATG', alt_aa='M', lower=0.5, upper=0.5,
                       forced_fraction=1.0, accepted=True, member_indices=(0,)),
        ]
        ann = self._ann('M', 1.0, combined_states=states)
        result = match_rules([ann], [rule_i])
        assert len(result[0].rule_matches) == 1
        assert result[0].rule_matches[0].mutation == 'I'
        # Combined-state hit -> effect lower is the state's lower (0.5), not row AF (1.0).
        assert result[0].rule_effect_aa_freq[10] == pytest.approx(0.5)

    def test_rule_effect_alt_records_combined_state_aa(self) -> None:
        """When a rule fires via a combined state whose alt_aa differs from the
        single-exchange, ``rule_effect_alt`` records the combined-state alt_aa
        so the report can display the amino acid that actually matched.
        """
        rule_i = self._rule(10, 'I')
        states = [
            CodonState(alt_codon='ATT', alt_aa='I', lower=0.5, upper=0.5,
                       forced_fraction=1.0, accepted=True, member_indices=(0, 1)),
        ]
        ann = self._ann('M', 1.0, combined_states=states)
        result = match_rules([ann], [rule_i])
        assert result[0].rule_effect_alt[10] == 'I'

    def test_rule_effect_alt_records_single_aa_for_single_hit(self) -> None:
        """When a rule fires via the single-exchange, ``rule_effect_alt`` records
        the single-exchange alt_aa (== ann.alt_aa)."""
        rule_n = self._rule(70, 'N')
        ann = self._ann('N', 0.9)
        result = match_rules([ann], [rule_n])
        assert result[0].rule_effect_alt[70] == 'N'

    def test_rule_fires_via_single_and_combined_uses_combined_lower(self) -> None:
        """K20M fires on A->T row via both single(M) and combined(M@0.5); the combined
        lower (0.5) is recorded since the combinatorial effect is what the report shows."""
        rule_m = self._rule(20, 'M')
        states = [
            CodonState(alt_codon='ATT', alt_aa='I', lower=0.5, upper=0.5,
                       forced_fraction=1.0, accepted=True, member_indices=(0, 1)),
            CodonState(alt_codon='ATG', alt_aa='M', lower=0.5, upper=0.5,
                       forced_fraction=1.0, accepted=True, member_indices=(0,)),
        ]
        ann = self._ann('M', 1.0, combined_states=states)
        result = match_rules([ann], [rule_m])
        assert len(result[0].rule_matches) == 1
        assert result[0].rule_effect_aa_freq[20] == pytest.approx(0.5)

    def test_rule_fires_on_promoted_single_uses_row_af(self) -> None:
        """G->T row (single=I promoted from forced combined, combined_states=[]):
        K20I fires on single=I; effect lower is the row's own AF (0.5)."""
        rule_i = self._rule(10, 'I')
        ann = self._ann('I', 0.5, combined_states=[])
        result = match_rules([ann], [rule_i])
        assert len(result[0].rule_matches) == 1
        assert result[0].rule_effect_aa_freq[10] == pytest.approx(0.5)

    def test_x_rule_does_not_fire(self) -> None:
        """A K20X rule does not fire (no X state is ever emitted)."""
        rule_x = self._rule(30, 'X')
        states = [
            CodonState(alt_codon='ATT', alt_aa='I', lower=0.5, upper=0.5,
                       forced_fraction=1.0, accepted=True, member_indices=(0, 1)),
        ]
        ann = self._ann('M', 1.0, combined_states=states)
        result = match_rules([ann], [rule_x])
        assert len(result[0].rule_matches) == 0

    def test_synonymous_single_not_matched_even_with_combined(self) -> None:
        """A synonymous single-exchange is not matched, but a non-synonymous combined
        state on the same annotation still fires."""
        rule = self._rule(40, 'I')
        states = [
            CodonState(alt_codon='ATT', alt_aa='I', lower=0.5, upper=0.5,
                       forced_fraction=1.0, accepted=True, member_indices=(0, 1)),
        ]
        ann = self._ann('K', 1.0, combined_states=states, )
        ann.consequence = 'synonymous'
        result = match_rules([ann], [rule])
        # Synonymous single is skipped, but combined state I should still fire.
        assert len(result[0].rule_matches) == 1
        assert result[0].rule_effect_aa_freq[40] == pytest.approx(0.5)

    def test_single_exchange_with_zero_lower_does_not_match_rule(self) -> None:
        """A combined member whose single-exchange state is Fréchet-impossible
        (single_exchange_aa_freq == 0) must NOT match a rule keyed on that single
        AA, even though its marginal allele_freq is high. The single exchange is
        guaranteed absent from the population; only its combined states can fire.

        Regression for the bug where match_rules used the marginal allele_freq
        for the single-exchange candidate, classifying a guaranteed-absent single
        as a high-AF resistance hit.
        """
        rule_single = self._rule(50, 'E')  # keys on the single-exchange AA
        ann = self._ann('E', 0.9, combined_states=[])  # marginal 0.9
        ann.is_combined_codon_event = True
        ann.single_exchange_aa_freq = 0.0  # single guaranteed absent
        result = match_rules([ann], [rule_single])
        # The single-exchange rule must NOT fire: its population frequency is 0.
        assert len(result[0].rule_matches) == 0

    def test_single_exchange_with_nonzero_lower_matches_at_single_lower(self) -> None:
        """A combined member whose single-exchange is Fréchet-possible
        (single_exchange_aa_freq > 0) matches a single-AA rule, but the recorded
        effect lower is single_exchange_aa_freq, not the marginal allele_freq."""
        rule_single = self._rule(60, 'M')
        ann = self._ann('M', 0.99, combined_states=[])  # marginal 0.99
        ann.is_combined_codon_event = True
        ann.single_exchange_aa_freq = 0.09  # Fréchet lower of the single state
        result = match_rules([ann], [rule_single])
        assert len(result[0].rule_matches) == 1
        assert result[0].rule_effect_aa_freq[60] == pytest.approx(0.09)

    def test_non_combined_single_uses_allele_freq(self) -> None:
        """A non-combined annotation (single_exchange_aa_freq == allele_freq) still
        matches at its allele_freq — no regression for the common case."""
        rule = self._rule(70, 'N')
        ann = self._ann('N', 0.9)
        # single_exchange_aa_freq defaults to allele_freq for non-combined
        assert ann.single_exchange_aa_freq == pytest.approx(0.9)
        result = match_rules([ann], [rule])
        assert len(result[0].rule_matches) == 1
        assert result[0].rule_effect_aa_freq[70] == pytest.approx(0.9)

    def test_combined_state_with_zero_lower_not_accepted_does_not_match(self) -> None:
        """A combined state whose amino-acid frequency is 0 (Fréchet lower = 0,
        accepted = False) must NOT match a rule, even if hand-constructed on the
        annotation. In production such states never reach ``combined_states``
        (they are filtered out by ``accepted`` in _annotate_combined_snp_codon),
        but match_rules must still defend against them.

        Pins the invariant that every effect_candidate carries a strictly-positive
        amino-acid frequency.
        """
        rule = self._rule(80, 'I')
        # A CodonState with lower=0 and accepted=False — the Fréchet-impossible case.
        rejected_state = CodonState(
            alt_codon='ATT', alt_aa='I', lower=0.0, upper=0.5,
            forced_fraction=0.0, accepted=False, member_indices=(0, 1),
        )
        ann = self._ann('M', 0.9, combined_states=[rejected_state])
        ann.is_combined_codon_event = True
        ann.single_exchange_aa_freq = 0.0  # single also guaranteed absent
        result = match_rules([ann], [rule])
        # Neither the single (lower=0) nor the rejected combined state fires.
        assert len(result[0].rule_matches) == 0

    def test_non_combined_zero_allele_freq_does_not_match(self) -> None:
        """A non-combined annotation with allele_freq = 0 (hence
        single_exchange_aa_freq = 0 via __post_init__ default) must not match a
        rule. The amino acid is absent from the population."""
        rule = self._rule(90, 'N')
        ann = self._ann('N', 0.0)
        # single_exchange_aa_freq defaults to allele_freq (0.0) for non-combined.
        assert ann.single_exchange_aa_freq == pytest.approx(0.0)
        result = match_rules([ann], [rule])
        assert len(result[0].rule_matches) == 0


class TestMatchFormulaRules:
    def _make_ann(self, feature: str, codon_pos: int, ref_aa: str, alt_aa: str) -> AnnotatedVariant:
        return AnnotatedVariant(
            variant=VariantCall(
                chrom='ref',
                pos=codon_pos * 3,
                ref='A',
                alt='T',
                allele_freq=0.9,
                depth=100,
            ),
            feature_name=feature,
            codon_pos=codon_pos,
            ref_aa=ref_aa,
            alt_aa=alt_aa,
            consequence='missense',
        )

    def _atomic_rule(self, external_id: str, mutation: str, *, position: int = 1) -> ResistanceRule:
        return ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
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

        ann_a = self._make_ann('gag', 1, 'K', 'E')
        ann_b = self._make_ann('gag', 5, 'A', 'V')
        ann_a.rule_matches = [mut_a]
        ann_b.rule_matches = [mut_b]

        hits = match_formula_rules([ann_a, ann_b], [formula], member_af_threshold=0.75)

        assert len(hits) == 1
        assert hits[0].rule_set.group_name == 'Formula 1'
        assert {v.alt_aa for v in hits[0].matched_variants} == {'E', 'V'}

    def test_formula_member_with_zero_amino_acid_freq_does_not_contribute(self) -> None:
        """A formula member whose amino-acid frequency is 0 (single_exchange_aa_freq
        = 0, e.g. a Fréchet-impossible combined single) must not contribute to a
        formula hit, even though its nucleotide frequency (allele_freq) is high.

        The gate is indirect: match_rules must not place a rule match on the
        annotation (single_exchange_aa_freq = 0 -> single candidate skipped), so
        the member never enters best_ann_by_member and member_truth stays False.
        This test pins the end-to-end invariant: a high-nucleotide-frequency /
        zero-amino-acid-frequency member cannot satisfy an AND formula.
        """
        mut_a = self._atomic_rule('mut_a', 'E', position=1)
        mut_b = self._atomic_rule('mut_b', 'V', position=5)
        formula = self._formula('(mut_a AND mut_b)', [mut_a, mut_b])

        ann_a = self._make_ann('gag', 1, 'K', 'E')
        ann_a.is_combined_codon_event = True
        ann_a.single_exchange_aa_freq = 0.0  # amino-acid freq 0; nucleotide freq 0.9
        # Run match_rules first — the single-exchange gate must skip ann_a.
        match_rules([ann_a], [mut_a])
        assert ann_a.rule_matches == [], (
            'a zero-amino-acid-frequency member must not receive a rule match'
        )

        ann_b = self._make_ann('gag', 5, 'A', 'V')
        ann_b.rule_matches = [mut_b]

        hits = match_formula_rules([ann_a, ann_b], [formula], member_af_threshold=0.75)
        # ann_a contributes nothing, so the AND formula cannot be satisfied.
        assert hits == []

    def test_formula_not_uses_af_gated_presence(self) -> None:
        mut_a = self._atomic_rule('mut_a', 'E', position=1)
        mut_b = self._atomic_rule('mut_b', 'V', position=5)
        formula = self._formula('(mut_a AND (NOT mut_b))', [mut_a, mut_b])

        ann_a = self._make_ann('gag', 1, 'K', 'E')
        ann_a.rule_matches = [mut_a]
        ann_b = self._make_ann('gag', 5, 'A', 'V')
        ann_b.variant.allele_freq = 0.60
        ann_b.rule_matches = [mut_b]

        hits = match_formula_rules([ann_a, ann_b], [formula], member_af_threshold=0.75)

        assert len(hits) == 1
        assert {v.alt_aa for v in hits[0].matched_variants} == {'E'}

    def test_formula_or_prefers_highest_af_branch(self) -> None:
        mut_a = self._atomic_rule('mut_a', 'E', position=1)
        mut_b = self._atomic_rule('mut_b', 'V', position=5)
        formula = self._formula('(mut_a OR mut_b)', [mut_a, mut_b])

        ann_a = self._make_ann('gag', 1, 'K', 'E')
        ann_a.variant.allele_freq = 0.80
        ann_a.rule_matches = [mut_a]
        ann_b = self._make_ann('gag', 5, 'A', 'V')
        ann_b.variant.allele_freq = 0.95
        ann_b.rule_matches = [mut_b]

        hits = match_formula_rules([ann_a, ann_b], [formula], member_af_threshold=0.75)

        assert len(hits) == 1
        assert len(hits[0].matched_variants) == 1
        assert hits[0].matched_variants[0].alt_aa == 'V'


class TestInsAnyRuleMatching:
    def _make_ins_rule(self, mutation: str = 'INS_any') -> ResistanceRule:
        return ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='',
            position=4,
            reference='F',
            mutation=mutation,
            phenotype='resistant',
        )

    def _make_ann(self, consequence: str, alt_aa: str = 'FGG') -> AnnotatedVariant:
        return AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=12, ref='A', alt='T', allele_freq=0.9, depth=100),
            feature_name='gag',
            codon_pos=4,
            ref_aa='F',
            alt_aa=alt_aa,
            consequence=consequence,
        )

    def test_ins_any_matches_any_insertion(self):
        ann = self._make_ann('insertion')
        result = match_rules([ann], [self._make_ins_rule()])
        assert result[0].is_resistance_hit

    def test_ins_any_does_not_match_frameshift(self):
        ann = self._make_ann('frameshift', alt_aa='FfsX')
        result = match_rules([ann], [self._make_ins_rule()])
        assert not result[0].is_resistance_hit

    def test_ins_any_does_not_match_deletion(self):
        ann = self._make_ann('deletion', alt_aa='F')
        result = match_rules([ann], [self._make_ins_rule()])
        assert not result[0].is_resistance_hit

    def test_specific_insertion_takes_precedence_over_ins_any(self):
        specific_rule = ResistanceRule(
            id=1,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='',
            position=4,
            reference='F',
            mutation='FGG',
            phenotype='resistant',
        )
        wildcard_rule = ResistanceRule(
            id=2,
            feature_name='gag',
            feature_id=1,
            drug_name='DrugA',
            drug_id=1,
            reference_identifier='',
            position=4,
            reference='F',
            mutation='INS_any',
            phenotype='resistant',
        )
        ann = AnnotatedVariant(
            variant=VariantCall(chrom='ref', pos=12, ref='A', alt='T', allele_freq=0.9, depth=100),
            feature_name='gag',
            codon_pos=4,
            ref_aa='F',
            alt_aa='FGG',
            consequence='insertion',
        )
        result = match_rules([ann], [specific_rule, wildcard_rule])
        matched_mutations = [r.mutation for r in result[0].rule_matches]
        assert 'FGG' in matched_mutations
        assert 'INS_any' not in matched_mutations


class TestInsAnyRuleEndToEnd:
    """End-to-end tests for INS_any rule import, matching, and reporting."""

    def _setup_project(self, tmp_path: Path) -> tuple[Path, Path]:
        """Return (project_db, output_dir) for a project with an INS_any rule at codon 2."""
        genbank_path = tmp_path / 'tiny.gb'
        write_genbank(
            genbank_path,
            [
                {
                    'id': 'tiny_ref',
                    'accession': 'tiny_ref',
                    'sequence': TINY_REF_SEQ,
                    'features': [
                        {'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}
                    ],
                }
            ],
        )
        rules_tsv = tmp_path / 'rules.tsv'
        # Position 2 (1-based) = codon 1 (0-based) = K in TINY_REF_SEQ
        rules_tsv.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
            gag\ttiny_ref\t2\tK\tins_any\tDrugA\tresistant
        """))
        project_db = tmp_path / 'project.db'
        init_project(
            db_path=project_db,
            name='test_ins_any',
            genbank_paths=[genbank_path],
            rules_tsv=rules_tsv,
            additional_info=False,
        )
        return project_db, tmp_path / 'output'

    def test_ins_any_rule_imports_and_is_stored(self, tmp_path: Path) -> None:
        """INS_any rule should be stored in the DB with canonical mutation token."""
        project_db, _ = self._setup_project(tmp_path)
        conn = open_project_db(project_db)
        row = conn.execute(
            "SELECT mutation FROM resistance_rule WHERE position = 1"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row['mutation'] == 'INS_any'

    def test_ins_any_fires_for_in_frame_insertion_via_vcf(self, tmp_path: Path) -> None:
        """Profiling a VCF with an in-frame insertion at the INS_any position should fire the rule."""
        project_db, output_dir = self._setup_project(tmp_path)

        # TINY_REF_SEQ codon 1 (0-based) starts at nucleotide pos 3.
        # Insert 3 nt (AAA) after pos 3 (0-based) = pos 4 (1-based VCF).
        # VCF: REF=A (pos 4), ALT=AAAA (in-frame +3 nt insertion)
        ref_fasta = tmp_path / 'ref.fasta'
        ref_fasta.write_text(f'>tiny_ref\n{TINY_REF_SEQ}\n')

        vcf_path = tmp_path / 'ins.vcf'
        vcf_path.write_text(textwrap.dedent("""\
            ##fileformat=VCFv4.2
            ##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">
            ##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">
            #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
            tiny_ref\t4\t.\tA\tAAAA\t100\tPASS\tAF=0.95;DP=500
        """))

        result = CliRunner().invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(ref_fasta),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code == 0, result.output
        assert '1 total database hits' in result.output

    def test_ins_any_report_shows_rule_label_and_actual_allele(self, tmp_path: Path) -> None:
        """HTML report should show INS_any as rule label and actual inserted AA as allele."""
        project_db, output_dir = self._setup_project(tmp_path)

        ref_fasta = tmp_path / 'ref.fasta'
        ref_fasta.write_text(f'>tiny_ref\n{TINY_REF_SEQ}\n')

        vcf_path = tmp_path / 'ins_report.vcf'
        vcf_path.write_text(textwrap.dedent("""\
            ##fileformat=VCFv4.2
            ##INFO=<ID=AF,Number=A,Type=Float,Description="Allele Frequency">
            ##INFO=<ID=DP,Number=1,Type=Integer,Description="Read Depth">
            #CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO
            tiny_ref\t4\t.\tA\tAAAA\t100\tPASS\tAF=0.95;DP=500
        """))

        result = CliRunner().invoke(app, [
            'vcf',
            '--project', str(project_db),
            '--vcf', str(vcf_path),
            '--ref-fasta', str(ref_fasta),
            '--output', str(output_dir),
            '--min-af', '0.01',
            '--min-depth', '0',
        ])

        assert result.exit_code == 0, result.output
        html = (output_dir / 'ins_report.report.html').read_text()
        # Rule label in database hits tab should show INS_any
        assert 'INS_any' in html


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
                    'features': [{'feature': 'gag', 'protein': 'Gag', 'start': 1, 'end': 87, 'strand': '+'}],
                }
            ],
        )

        base_rules = tmp_path / 'base.tsv'
        base_rules.write_text(textwrap.dedent("""\
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
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
            feature\treference_identifier\tposition\treference\tmutation\tantiviral\tphenotype
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


