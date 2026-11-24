import { useMemo } from 'react';

import { isPopulated } from '../../../utils';

function renderPmidLinks(value) {
  const pmidText = String(value);
  const pmids = Array.from(new Set(pmidText.match(/\d+/g) || []));
  if (pmids.length === 0) {
    return pmidText;
  }
  return (
    <>
      {pmids.map((pmid, index) => (
        <span key={pmid}>
          {index > 0 ? ', ' : ''}
          <a href={`https://pubmed.ncbi.nlm.nih.gov/${pmid}/`} target="_blank" rel="noreferrer">
            PMID:{pmid}
          </a>
        </span>
      ))}
    </>
  );
}

function renderDatabaseMetaValue(entry) {
  const valueText = String(entry.value).trim();
  if (entry.key === 'website') {
    return <a href={valueText} target="_blank" rel="noreferrer">{valueText}</a>;
  }
  if (entry.key === 'publication_doi') {
    const doi = valueText.replace(/^https?:\/\/doi\.org\//i, '');
    return <a href={`https://doi.org/${doi}`} target="_blank" rel="noreferrer">{valueText}</a>;
  }
  if (entry.key === 'publication_pmid') {
    return renderPmidLinks(valueText);
  }
  if (entry.key === 'contact' && valueText.includes('@') && !valueText.includes('mailto:')) {
    return <a href={`mailto:${valueText}`}>{valueText}</a>;
  }
  return entry.value;
}

const SPEC_KEYS = ['maintainers', 'contact', 'publication_pmid', 'publication_doi', 'website', 'license'];
const PROVENANCE_KEYS = ['uuid', 'created_at', 'maintainer_update'];

export function DatabaseMetaPanel({ selectedDatabase }) {
  const databaseMeta = useMemo(() => {
    if (!selectedDatabase) {
      return null;
    }

    const metadata = selectedDatabase.metadata || {};
    const entries = [
      { key: 'display_name', label: 'Database name', value: selectedDatabase.display_name },
      { key: 'uuid', label: 'UUID', value: selectedDatabase.uuid },
      { key: 'created_at', label: 'Created at', value: selectedDatabase.created_at },
      { key: 'schema_version', label: 'Schema version', value: selectedDatabase.schema_version },
      { key: 'maintainers', label: 'Maintainers', value: metadata.maintainers },
      { key: 'contact', label: 'Contact', value: metadata.contact },
      { key: 'publication_pmid', label: 'Publication PMID', value: metadata.publication_pmid },
      { key: 'publication_doi', label: 'Publication DOI', value: metadata.publication_doi },
      { key: 'website', label: 'Website', value: metadata.website },
      { key: 'description', label: 'Description', value: metadata.description },
      { key: 'maintainer_update', label: 'Maintainer update', value: metadata.maintainer_update },
      { key: 'license', label: 'License', value: metadata.license },
    ].filter((entry) => isPopulated(entry.value));

    if (entries.length === 0) {
      return null;
    }

    const byKey = new Map(entries.map((entry) => [entry.key, entry]));
    const pick = (key) => byKey.get(key) || null;
    return {
      name: pick('display_name'),
      schemaVersion: pick('schema_version'),
      description: pick('description'),
      specs: SPEC_KEYS.map(pick).filter(Boolean),
      provenance: PROVENANCE_KEYS.map(pick).filter(Boolean),
    };
  }, [selectedDatabase]);

  if (!databaseMeta) {
    return null;
  }

  return (
    <section className="database-meta-panel" aria-label="Database information">
      {databaseMeta.name ? (
        <div className="database-meta-identity">
          <div className="database-meta-name-row">
            <span className="database-meta-name">{databaseMeta.name.value}</span>
            {databaseMeta.schemaVersion ? (
              <span className="database-meta-chip" title="Database schema version">
                v{String(databaseMeta.schemaVersion.value).replace(/^v/i, '')}
              </span>
            ) : null}
          </div>
          {databaseMeta.description ? (
            <p className="database-meta-description">{databaseMeta.description.value}</p>
          ) : null}
        </div>
      ) : null}

      {databaseMeta.specs.length > 0 ? (
        <div className="database-meta-specs">
          {databaseMeta.specs.map((entry) => (
            <div key={entry.key} className="database-meta-spec">
              <span className="database-meta-label">{entry.label}</span>
              <span className="database-meta-value">{renderDatabaseMetaValue(entry)}</span>
            </div>
          ))}
        </div>
      ) : null}

      {databaseMeta.provenance.length > 0 ? (
        <div className="database-meta-provenance">
          {databaseMeta.provenance.map((entry, index) => (
            <span key={entry.key} className="database-meta-provenance-item">
              {index > 0 ? <span className="database-meta-provenance-sep" aria-hidden="true">·</span> : null}
              <span className="database-meta-value">
                <span className="database-meta-provenance-label">{entry.label}</span>{' '}
                {renderDatabaseMetaValue(entry)}
              </span>
            </span>
          ))}
        </div>
      ) : null}
    </section>
  );
}
