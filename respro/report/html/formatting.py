"""Formatting, badge-class, bibliography, and cached asset helpers for HTML reports."""

from __future__ import annotations

from functools import cache, lru_cache
from pathlib import Path
from urllib.parse import quote

from respro.config.cli_settings import CLI_CONFIG, CliConfig
from respro.core.phenotype_ranks import label_to_rank
from respro.db.models import Publication
from respro.db.report_queries import parse_numeric_value

_REPORT_DIR = Path(__file__).resolve().parent.parent

_PHENOTYPE_BADGE_CLASS_BY_RANK: dict[int, str] = {
    1: 'phenotype--susceptible',
    2: 'phenotype--potential',
    3: 'phenotype--low-level',
    4: 'phenotype--intermediate',
    5: 'phenotype--resistant',
    0: 'phenotype--unknown',
    -1: 'phenotype--contradictory',
}


@cache
def load_svg_data_url(asset_name: str) -> str:
    """Load an SVG asset from ``respro/report/static/assets`` as a data URL."""
    svg_text = (_REPORT_DIR / 'static' / 'assets' / asset_name).read_text(encoding='utf-8')
    return f'data:image/svg+xml,{quote(svg_text)}'


@lru_cache(maxsize=1)
def load_html_template_text() -> str:
    """Load the main HTML report Jinja template."""
    return (_REPORT_DIR / 'templates' / 'report.html.j2').read_text(encoding='utf-8')


@lru_cache(maxsize=1)
def load_html_css_text() -> str:
    """Load the main HTML report stylesheet."""
    return (_REPORT_DIR / 'static' / 'report.css').read_text(encoding='utf-8')


@lru_cache(maxsize=1)
def load_html_js_text() -> str:
    """Load the main HTML report client script."""
    return (_REPORT_DIR / 'static' / 'report.js').read_text(encoding='utf-8')


def bin_for_af(af: float, is_fasta_mode: bool = False, cfg: CliConfig = CLI_CONFIG) -> str:
    """Return the configured AF-bin label for *af*."""
    bins = (cfg.af_bins_fasta if is_fasta_mode else cfg.af_bins).as_dict()
    for label, (lo, hi) in sorted(bins.items(), key=lambda x: -x[1][0]):
        if lo <= af <= hi:
            return label
    return ''


def assign_numeric_tier(value: float, mean: float, std: float) -> str:
    """Map a numeric metric value to a severity tier CSS class using mean + k*std thresholds."""
    if value <= mean:
        return 'metric--tier1'
    if value <= mean + std:
        return 'metric--tier2'
    if value <= mean + 2 * std:
        return 'metric--tier3'
    return 'metric--tier4'


def format_drug_name_with_alias(name: str, alias_map: dict[str, str]) -> str:
    """Return canonical drug name with `` (alias)`` suffix when an alias exists."""
    alias = alias_map.get(name.strip().lower(), '')
    return f'{name} ({alias})' if alias else name


def phenotype_badge_class(label: str) -> str:
    """Return the CSS badge class for a phenotype label inferred from its severity rank."""
    if not label:
        return ''
    rank = label_to_rank(label)
    return _PHENOTYPE_BADGE_CLASS_BY_RANK.get(rank, '') if rank is not None else ''


def build_rule_metrics(
    phenotype: str,
    clinical_phenotype: str,
    ic50: str,
    fold_ic50: str,
    score: str,
    thresholds: dict[str, tuple[float, float] | None] | None = None,
) -> list[dict]:
    """Build metric chips for non-empty, non-unknown resistance fields."""
    metrics: list[dict] = []
    if phenotype and phenotype.lower() != 'unknown':
        metrics.append({'label': 'Phenotype', 'value': phenotype, 'badge_class': phenotype_badge_class(phenotype)})
    if clinical_phenotype and clinical_phenotype.lower() != 'unknown':
        metrics.append({
            'label': 'Clinical phenotype',
            'value': clinical_phenotype,
            'badge_class': phenotype_badge_class(clinical_phenotype),
        })

    for field, label, value in (('ic50', 'IC50', ic50), ('fold_ic50', 'Fold IC50', fold_ic50), ('score', 'Score', score)):
        if not value:
            continue
        badge_class = ''
        if thresholds and (stats := thresholds.get(field)) is not None:
            if (parsed := parse_numeric_value(value)) is not None:
                badge_class = assign_numeric_tier(parsed, *stats)
        metrics.append({'label': label, 'value': value, 'badge_class': badge_class})
    return metrics


def _publication_key(pub: Publication) -> tuple:
    """Return a stable deduplication key for a publication."""
    pub_id = int(getattr(pub, 'id', 0) or 0)
    if pub_id > 0:
        return ('id', str(pub_id), '', '', '')
    return (
        'meta',
        (pub.doi or '').strip().lower(),
        (pub.pubmed_id or '').strip(),
        (pub.raw_input or '').strip().lower(),
        (pub.title or '').strip().lower(),
    )


def _publication_url(pub: Publication) -> str:
    """Return DOI or PubMed URL for a publication, or ``''`` when unavailable."""
    if pub.doi:
        return f"https://doi.org/{quote(pub.doi.strip(), safe='/:')}"
    if pub.pubmed_id:
        return f"https://pubmed.ncbi.nlm.nih.gov/{quote(pub.pubmed_id.strip(), safe='')}/"
    return ''


def _format_publication_label(pub: Publication) -> str:
    """Build a human-readable bibliography label from available publication fields."""
    prefix_parts = [
        part
        for part in (
            f'{pub.first_author} et al.' if pub.first_author else '',
            pub.year,
            pub.journal,
        )
        if part
    ]
    prefix = ', '.join(prefix_parts)
    body = pub.title or pub.doi or pub.pubmed_id or pub.raw_input
    if prefix and body:
        return f'{prefix}: {body}'
    return prefix or body


def attach_citations(rows: list[dict]) -> list[dict]:
    """Deduplicate ``_raw_pubs`` across *rows*, attach ``pub_citations``, and return the bibliography."""
    pub_to_num: dict[tuple, int] = {}
    bibliography: list[dict] = []
    for row in rows:
        for pub in row.get('_raw_pubs', []):
            key = _publication_key(pub)
            if key in pub_to_num:
                continue
            num = len(bibliography) + 1
            pub_to_num[key] = num
            url = _publication_url(pub)
            bibliography.append({
                'num': num,
                'url': url,
                'label': _format_publication_label(pub),
                'has_url': bool(url),
            })

    for row in rows:
        citations: list[dict] = []
        seen_nums: set[int] = set()
        for pub in row.pop('_raw_pubs', []):
            cit_num = pub_to_num.get(_publication_key(pub))
            if cit_num is None or cit_num in seen_nums:
                continue
            seen_nums.add(cit_num)
            citations.append({'num': cit_num, 'url': _publication_url(pub)})
        row['pub_citations'] = citations

    return bibliography


def metric_flags_for_rows(rows: list[dict]) -> dict[str, bool]:
    """Return ``has_*_metrics`` visibility flags for a table's rows."""
    present = {metric['label'] for row in rows for metric in row.get('metrics', [])}
    return {
        'has_phenotype_metrics': 'Phenotype' in present,
        'has_clinical_phenotype_metrics': 'Clinical phenotype' in present,
        'has_ic50_metrics': 'IC50' in present,
        'has_fold_ic50_metrics': 'Fold IC50' in present,
        'has_score_metrics': 'Score' in present,
    }


def join_english_list(items: list[str]) -> str:
    """Join strings with commas and a final Oxford ``and``."""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f'{items[0]} and {items[1]}'
    return ', '.join(items[:-1]) + f', and {items[-1]}'
