import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { ArtifactTableCells } from './ArtifactTableCells';

function renderInRow(props) {
  return render(
    <table>
      <tbody>
        <tr>
          <ArtifactTableCells {...props} />
        </tr>
      </tbody>
    </table>,
  );
}

describe('ArtifactTableCells', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders View and Download links with target="_blank" and rel="noreferrer" when paths are provided', () => {
    const buildReportUrl = vi.fn((p) => `/api/report?artifact_id=${p}`);
    const buildArtifactUrl = vi.fn((p) => `/api/artifact?artifact_id=${p}`);

    renderInRow({
      htmlPath: 'h1',
      pdfPath: 'p1',
      jsonPath: 'j1',
      tsvPath: 't1',
      buildReportUrl,
      buildArtifactUrl,
    });

    expect(buildReportUrl).toHaveBeenCalledWith('h1');
    expect(buildArtifactUrl).toHaveBeenCalledWith('p1');
    expect(buildArtifactUrl).toHaveBeenCalledWith('j1');
    expect(buildArtifactUrl).toHaveBeenCalledWith('t1');

    const viewLink = screen.getByRole('link', { name: 'View' });
    expect(viewLink).toHaveAttribute('href', '/api/report?artifact_id=h1');
    expect(viewLink).toHaveAttribute('target', '_blank');
    expect(viewLink).toHaveAttribute('rel', 'noreferrer');

    const downloadLinks = screen.getAllByRole('link', { name: 'Download' });
    expect(downloadLinks).toHaveLength(3);
    expect(downloadLinks.map((a) => a.getAttribute('href'))).toEqual([
      '/api/artifact?artifact_id=p1',
      '/api/artifact?artifact_id=j1',
      '/api/artifact?artifact_id=t1',
    ]);
    for (const link of downloadLinks) {
      expect(link).toHaveAttribute('target', '_blank');
      expect(link).toHaveAttribute('rel', 'noreferrer');
    }
  });

  it('renders em-dash fallbacks for missing artifact paths without calling URL builders', () => {
    const buildReportUrl = vi.fn();
    const buildArtifactUrl = vi.fn();

    renderInRow({
      htmlPath: '',
      pdfPath: null,
      jsonPath: undefined,
      tsvPath: '',
      buildReportUrl,
      buildArtifactUrl,
    });

    expect(buildReportUrl).not.toHaveBeenCalled();
    expect(buildArtifactUrl).not.toHaveBeenCalled();
    expect(screen.queryByRole('link')).toBeNull();
    const cells = screen.getAllByRole('cell');
    expect(cells).toHaveLength(4);
    for (const cell of cells) {
      expect(cell).toHaveTextContent('—');
    }
  });
});
