"""
Tests for VCF remapping — indel strand handling and query_ref_codon population.
"""

from respro.core.annotate_vcf import reverse_complement
from respro.core.profile_vcf import _transform_allele, remap_variants
from respro.core.sequence_matching import GeneMatch
from respro.db.models import GeneRecord, VariantCall


# ─── _transform_allele ────────────────────────────────────────────────

class TestTransformAllele:
    """Unit tests for the VCF allele strand-flip helper."""

    def test_snp_need_comp_false(self) -> None:
        assert _transform_allele('A', need_comp=False) == 'A'

    def test_snp_need_comp_true(self) -> None:
        # Single-base: just complement.
        assert _transform_allele('A', need_comp=True) == 'T'
        assert _transform_allele('C', need_comp=True) == 'G'

    def test_insertion_allele_need_comp_false(self) -> None:
        # Multi-base: untouched when no flip needed.
        assert _transform_allele('AGGG', need_comp=False) == 'AGGG'

    def test_insertion_allele_need_comp_true(self) -> None:
        # complement(anchor) + RC(payload): complement('A')='T', RC('GGG')='CCC'
        assert _transform_allele('AGGG', need_comp=True) == 'TCCC'

    def test_deletion_ref_need_comp_true(self) -> None:
        # complement('C')='G', RC('TGG')='CCA'
        assert _transform_allele('CTGG', need_comp=True) == 'GCCA'

    def test_single_char_allele_need_comp_true(self) -> None:
        # anchor only, no payload — same as SNP complement
        assert _transform_allele('G', need_comp=True) == 'C'

    def test_empty_allele_unchanged(self) -> None:
        assert _transform_allele('', need_comp=True) == ''


# ─── Helpers ──────────────────────────────────────────────────────────

def _make_match(gene: GeneRecord, strand: str, query_len: int) -> GeneMatch:
    """Build a perfect-identity GeneMatch with a simple identity CIGAR."""
    cds_len = gene.end - gene.start
    return GeneMatch(
        gene=gene,
        identity=1.0,
        cds_coverage=1.0,
        query_coverage=1.0,
        query_start=0,
        query_end=query_len,
        strand=strand,
        cigar=f'{cds_len}M',
    )


# ─── remap_variants — forward strand gene ─────────────────────────────

class TestRemapVariantsInsertion:
    """remap_variants correctly passes through and transforms indels."""

    def _plus_gene(self) -> GeneRecord:
        # ATG GGG TTT → M G F (9 nt, forward strand)
        return GeneRecord(
            id=1, reference_id=1, name='gene', protein='P',
            start=0, end=9, strand='+', codon_start=0, nt_sequence='ATGGGGTTT',
        )

    def _minus_gene(self) -> GeneRecord:
        # CDS 'ATGGGGTTT' (M G F), gene on minus strand.
        # Genomic forward = RC('ATGGGGTTT') = 'AAACCCAT'... computed precisely:
        # complement(ATGGGGTTT) = TACCCCAAA, reversed = AAACCCAT... 9 chars:
        # A-T-G-G-G-G-T-T-T → complement → T-A-C-C-C-C-A-A-A → reversed → AAACCCCAT
        return GeneRecord(
            id=1, reference_id=1, name='gene', protein='P',
            start=0, end=9, strand='-', codon_start=0, nt_sequence='ATGGGGTTT',
        )

    def test_inframe_insertion_plus_gene_plus_match(self) -> None:
        """Forward gene, forward alignment: allele passes through, query_ref_codon set."""
        gene = self._plus_gene()
        query = 'ATGGGGTTT'  # identical to CDS
        match = _make_match(gene, '+', len(query))

        var = VariantCall(chrom='c', pos=3, ref='G', alt='GGGG', allele_freq=0.9, depth=100)
        remapped, warnings = remap_variants([var], [match], query)

        assert not warnings
        assert len(remapped) == 1
        r = remapped[0]
        assert r.pos == 3
        assert r.ref == 'G'
        assert r.alt == 'GGGG'
        # Codon 1 = query[3:6] = 'GGG'; match.strand='+': no complement
        assert r.query_ref_codon == 'GGG'

    def test_inframe_insertion_plus_gene_divergent_query_codon(self) -> None:
        """Divergent anchor codon in query is captured in query_ref_codon."""
        gene = self._plus_gene()
        # User's query has 'A' at codon 1 pos 0 instead of 'G' → codon 'AGG' → R
        query = 'ATGAGGTTT'
        match = _make_match(gene, '+', len(query))

        var = VariantCall(chrom='c', pos=3, ref='A', alt='AGGG', allele_freq=0.9, depth=100)
        remapped, warnings = remap_variants([var], [match], query)

        assert not warnings
        assert len(remapped) == 1
        assert remapped[0].query_ref_codon == 'AGG'

    def test_inframe_insertion_minus_gene_minus_match(self) -> None:
        """Minus gene, minus-strand alignment (need_comp=False): allele unchanged, codon set."""
        gene = self._minus_gene()
        # Genomic forward for this gene: AAACCCCAT (9 nt).
        # The RC of the query aligns forward to the CDS → match.strand='-'.
        genomic_fwd = reverse_complement('ATGGGGTTT')  # = 'AAACCCCAT'
        query = genomic_fwd
        match = _make_match(gene, '-', len(query))

        # Anchor at CDS pos 3 → genomic pos = (9-1)-3 = 5.
        # On genomic forward, pos 5 = 'C' (from AAACCCCAT: A=0,A=1,A=2,C=3,C=4,C=5,C=6,A=7,T=8).
        # Insert 'CCC' in forward direction → coding = RC('CCC') = 'GGG' → G.
        var = VariantCall(chrom='c', pos=5, ref='C', alt='CCCC', allele_freq=0.9, depth=100)
        remapped, warnings = remap_variants([var], [match], query)

        assert not warnings
        assert len(remapped) == 1
        r = remapped[0]
        # need_comp = (match.strand '-' != gene.strand '-') = False → allele unchanged
        assert r.ref == 'C'
        assert r.alt == 'CCCC'
        assert r.pos == 5
        # query_ref_codon for CDS pos 3 (codon 1 = 'GGG' in coding orientation):
        # match.strand='-': complement applied → 'GGG'
        assert r.query_ref_codon == 'GGG'

    def test_inframe_insertion_plus_gene_minus_match_transforms_allele(self) -> None:
        """Plus gene, minus-strand alignment (need_comp=True): allele is anchor-complement + RC payload."""
        gene = self._plus_gene()
        # Query aligns in RC to the CDS → match.strand='-', gene.strand='+' → need_comp=True.
        # User's "reference" is the RC of the CDS: RC('ATGGGGTTT') = 'AAACCCCAT'
        query = reverse_complement('ATGGGGTTT')  # = 'AAACCCCAT'
        match = _make_match(gene, '-', len(query))

        # In the RC-query space, the alignment maps RC positions to CDS positions.
        # RC-query[0] aligns to CDS[0] (A). Forward-query pos = query_len-1 - 0 = 8.
        # So q2c[8]=0, q2c[7]=1, ..., q2c[0]=8.
        # CDS pos 3 → forward-query pos = 8-3=5. genomic_pos = gene.start + 3 = 3.
        # On query (AAACCCCAT), pos 5 = 'C'.
        # Insertion of 'GGG' at forward-query pos 5: ref='C', alt='CGGG'.
        # need_comp=True → _transform_allele:
        #   ref = complement('C') = 'G'
        #   alt = complement('C') + RC('GGG') = 'G' + 'CCC' = 'GCCC'
        var = VariantCall(chrom='c', pos=5, ref='C', alt='CGGG', allele_freq=0.9, depth=100)
        remapped, warnings = remap_variants([var], [match], query)

        assert not warnings
        assert len(remapped) == 1
        r = remapped[0]
        assert r.pos == 3   # genomic_pos on internal forward
        assert r.ref == 'G'
        assert r.alt == 'GCCC'
        # query_ref_codon for CDS pos 3: forward positions 5,4,3 → query 'C','C','C' = 'CCC'
        # match.strand='-': complement → 'GGG'
        assert r.query_ref_codon == 'GGG'

    def test_frameshift_insertion_remapped(self) -> None:
        """1-nt insertion is remapped (not skipped) and survives to annotation."""
        gene = self._plus_gene()
        query = 'ATGGGGTTT'
        match = _make_match(gene, '+', len(query))

        var = VariantCall(chrom='c', pos=3, ref='G', alt='GG', allele_freq=0.9, depth=100)
        remapped, warnings = remap_variants([var], [match], query)

        assert not warnings
        assert len(remapped) == 1
        assert remapped[0].ref == 'G'
        assert remapped[0].alt == 'GG'


# ─── remap_variants — deletion ────────────────────────────────────────

class TestRemapVariantsDeletion:
    """remap_variants correctly handles deletions."""

    def _plus_gene(self) -> GeneRecord:
        return GeneRecord(
            id=1, reference_id=1, name='gene', protein='P',
            start=0, end=9, strand='+', codon_start=0, nt_sequence='ATGGGGTTT',
        )

    def test_inframe_deletion_plus_gene_plus_match(self) -> None:
        gene = self._plus_gene()
        query = 'ATGGGGTTT'
        match = _make_match(gene, '+', len(query))

        # Delete 'GGG' (codon 2) after anchor 'G' (codon 1 pos 0)
        var = VariantCall(chrom='c', pos=3, ref='GGGG', alt='G', allele_freq=0.9, depth=100)
        remapped, warnings = remap_variants([var], [match], query)

        assert not warnings
        assert len(remapped) == 1
        r = remapped[0]
        assert r.ref == 'GGGG'
        assert r.alt == 'G'
        assert r.query_ref_codon == 'GGG'

    def test_inframe_deletion_plus_gene_minus_match_transforms_allele(self) -> None:
        """need_comp=True: anchor complement + RC of deleted payload."""
        gene = self._plus_gene()
        query = reverse_complement('ATGGGGTTT')  # = 'AAACCCCAT'
        match = _make_match(gene, '-', len(query))

        # q2c[5]=3, pos 5 on query = 'C'. Delete 'CCC' after anchor.
        # ref='CCCC', alt='C'.
        # need_comp=True → ref = complement('C') + RC('CCC') = 'G' + 'GGG' = 'GGGG',
        #                   alt = complement('C') = 'G'.
        var = VariantCall(chrom='c', pos=5, ref='CCCC', alt='C', allele_freq=0.9, depth=100)
        remapped, warnings = remap_variants([var], [match], query)

        assert not warnings
        assert len(remapped) == 1
        r = remapped[0]
        assert r.pos == 3
        assert r.ref == 'GGGG'
        assert r.alt == 'G'
        assert r.query_ref_codon == 'GGG'

    def test_anchor_ref_mismatch_produces_warning(self) -> None:
        """VCF anchor base disagreeing with query FASTA emits a warning."""
        gene = self._plus_gene()
        query = 'ATGGGGTTT'
        match = _make_match(gene, '+', len(query))

        # Wrong anchor: pos=3 has 'G' in query, but REF says 'C'.
        var = VariantCall(chrom='c', pos=3, ref='CGGG', alt='C', allele_freq=0.9, depth=100)
        remapped, warnings = remap_variants([var], [match], query)

        assert len(remapped) == 0
        assert len(warnings) == 1
        assert 'REF' in warnings[0] or 'anchor' in warnings[0].lower() or '≠' in warnings[0]
