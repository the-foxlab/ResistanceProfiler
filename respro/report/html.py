"""
HTML report generation using Jinja2 templates.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from jinja2 import Environment, BaseLoader

from respro.db.models import GeneRecord
from respro.report.results_model import ProfilingResult

logger = logging.getLogger(__name__)

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ResistanceProfiler — {{ summary.project_name }}</title>
<style>
  :root { --accent: #2c3e50; --hit: #e74c3c; --ok: #27ae60; --bg: #fafbfc; }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', system-ui, sans-serif; color: #333; background: var(--bg); padding: 2rem; line-height: 1.5; }
  h1 { color: var(--accent); margin-bottom: .5rem; }
  h2 { margin: 1.5rem 0 .5rem; color: var(--accent); border-bottom: 2px solid #eee; padding-bottom: .3rem; }
  .meta { background: #fff; border: 1px solid #e1e4e8; border-radius: 6px; padding: 1rem 1.5rem; margin-bottom: 1.5rem; display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: .5rem; }
  .meta dt { font-weight: 600; font-size: .85rem; color: #666; }
  .meta dd { margin-bottom: .4rem; }
  .stats { display: flex; gap: 1.5rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
  .stat-card { background: #fff; border: 1px solid #e1e4e8; border-radius: 6px; padding: 1rem 1.5rem; min-width: 150px; text-align: center; }
  .stat-card .number { font-size: 2rem; font-weight: 700; }
  .stat-card .label { font-size: .85rem; color: #666; }
  .stat-card.hit .number { color: var(--hit); }
  table { width: 100%; border-collapse: collapse; margin-bottom: 1.5rem; font-size: .9rem; }
  th { background: var(--accent); color: #fff; text-align: left; padding: .5rem .7rem; }
  td { padding: .45rem .7rem; border-bottom: 1px solid #e1e4e8; }
  tr:hover td { background: #f0f4f8; }
  .hit-row { background: #fdf2f2; }
  .hit-row:hover td { background: #fce8e8; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: .75rem; font-weight: 600; }
  .badge-high { background: #e74c3c; color: #fff; }
  .badge-intermediate { background: #f39c12; color: #fff; }
  .badge-low { background: #95a5a6; color: #fff; }
  .badge-resistance { background: #e74c3c; color: #fff; }
  .badge-missense { background: #3498db; color: #fff; }
  .badge-synonymous { background: #95a5a6; color: #fff; }
  .plot-container { text-align: center; margin: 1rem 0; }
  .plot-container img { max-width: 100%; border: 1px solid #e1e4e8; border-radius: 6px; }
  footer { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e1e4e8; font-size: .8rem; color: #999; }
</style>
</head>
<body>

<h1>🧬 ResistanceProfiler Report</h1>

<dl class="meta">
  <dt>Project</dt><dd>{{ summary.project_name }}</dd>
  <dt>Organism</dt><dd>{{ summary.organism or '—' }}</dd>
  <dt>Reference</dt><dd>{{ summary.reference }}</dd>
  <dt>Sample</dt><dd>{{ summary.sample or '—' }}</dd>
  <dt>VCF</dt><dd>{{ summary.vcf }}</dd>
  <dt>Timestamp</dt><dd>{{ summary.timestamp }}</dd>
</dl>

<div class="stats">
  <div class="stat-card"><div class="number">{{ summary.total_variants }}</div><div class="label">Total variants</div></div>
  <div class="stat-card"><div class="number">{{ summary.variants_in_cds }}</div><div class="label">In CDS</div></div>
  <div class="stat-card hit"><div class="number">{{ summary.resistance_hits }}</div><div class="label">Resistance hits</div></div>
</div>

{% if plot_data %}
<h2>Mutation overview</h2>
<div class="plot-container">
  <img src="data:image/svg+xml;base64,{{ plot_data }}" alt="Lollipop plot">
</div>
{% endif %}

{% if hit_rows %}
<h2>Resistance-associated mutations</h2>
<table>
<thead><tr>
  <th>Gene</th><th>AA change</th><th>Codon pos</th>
  <th>AF</th><th>AF bin</th><th>Drug(s)</th><th>Phenotype</th><th>Publication</th>
</tr></thead>
<tbody>
{% for r in hit_rows %}
<tr class="hit-row">
  <td>{{ r.gene }}</td>
  <td><strong>{{ r.ref_aa }}{{ r.codon_pos }}{{ r.alt_aa }}</strong></td>
  <td>{{ r.codon_pos }}</td>
  <td>{{ '%.3f'|format(r.allele_freq) }}</td>
  <td><span class="badge badge-{{ r.af_bin }}">{{ r.af_bin }}</span></td>
  <td>{{ r.drugs }}</td>
  <td>{{ r.phenotype }}</td>
  <td>{{ r.publication }}</td>
</tr>
{% endfor %}
</tbody>
</table>
{% endif %}

<h2>All CDS variants</h2>
<table>
<thead><tr>
  <th>Chrom</th><th>Pos</th><th>Ref</th><th>Alt</th>
  <th>Gene</th><th>AA change</th><th>Consequence</th>
  <th>AF</th><th>AF bin</th><th>Depth</th><th>Resistance</th>
</tr></thead>
<tbody>
{% for r in cds_rows %}
<tr class="{{ 'hit-row' if r.resistance_hit else '' }}">
  <td>{{ r.chrom }}</td><td>{{ r.pos }}</td><td>{{ r.ref }}</td><td>{{ r.alt }}</td>
  <td>{{ r.gene }}</td>
  <td>{% if r.ref_aa %}{{ r.ref_aa }}{{ r.codon_pos }}{{ r.alt_aa }}{% endif %}</td>
  <td><span class="badge badge-{{ r.consequence }}">{{ r.consequence }}</span></td>
  <td>{{ '%.3f'|format(r.allele_freq) }}</td>
  <td><span class="badge badge-{{ r.af_bin }}">{{ r.af_bin }}</span></td>
  <td>{{ r.depth }}</td>
  <td>{{ '✓' if r.resistance_hit else '' }}</td>
</tr>
{% endfor %}
</tbody>
</table>

<footer>
  Generated by <strong>ResistanceProfiler v{{ version }}</strong> on {{ summary.timestamp }}.
</footer>

</body>
</html>
"""


def render_html(
    result: ProfilingResult,
    genes: list[GeneRecord] | None = None,
    plot_svg_path: Path | None = None,
) -> str:
    """
    Render the profiling result to an HTML string.

    :param result: ProfilingResult object
    :param genes: optional list of genes for context
    :param plot_svg_path: optional path to embedded plot SVG
    :return: HTML string
    """
    from respro import __version__

    env = Environment(loader=BaseLoader(), autoescape=True)
    template = env.from_string(_HTML_TEMPLATE)

    summary = result.summary_dict()
    cds_rows = [r for r in result.variants_as_dicts() if r.get('gene')]
    hit_rows = []
    for r in cds_rows:
        if r.get('resistance_hit'):
            drugs = '; '.join(d['drug'] for d in r.get('drug_hits', []))
            phenotypes = '; '.join(set(d['phenotype'] for d in r.get('drug_hits', [])))
            publications = '; '.join(sorted(set(d['publication'] for d in r.get('drug_hits', []) if d.get('publication'))))
            hit_rows.append({**r, 'drugs': drugs, 'phenotype': phenotypes, 'publication': publications})

    # Embed plot as base64 if available
    plot_data = ''
    if plot_svg_path and Path(plot_svg_path).is_file():
        raw = Path(plot_svg_path).read_bytes()
        plot_data = base64.b64encode(raw).decode('ascii')

    return template.render(
        summary=summary,
        cds_rows=cds_rows,
        hit_rows=hit_rows,
        plot_data=plot_data,
        version=__version__,
    )


def write_html(
    result: ProfilingResult,
    output_path: Path,
    genes: list[GeneRecord] | None = None,
    plot_svg_path: Path | None = None,
) -> Path:
    """
    Render and write the HTML report to a file.

    :param result: ProfilingResult object
    :param output_path: path to write HTML file to
    :param genes: optional list of genes for context
    :param plot_svg_path: optional path to embedded plot SVG
    :return: path to written HTML file
    """
    html = render_html(result, genes=genes, plot_svg_path=plot_svg_path)
    output_path = Path(output_path)
    output_path.write_text(html, encoding='utf-8')
    logger.info('HTML report written to %s', output_path)
    return output_path

