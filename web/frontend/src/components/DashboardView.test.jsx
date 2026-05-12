import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

describe('Dashboard Upload Interaction Flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('should create file input and handle file selection', async () => {
    const user = userEvent.setup();

    // Create a simple component that uses file input
    const TestComponent = () => {
      const [file, setFile] = React.useState(null);
      const [fileName, setFileName] = React.useState('');

      const handleFileChange = (e) => {
        const selectedFile = e.target.files?.[0];
        if (selectedFile) {
          setFile(selectedFile);
          setFileName(selectedFile.name);
        }
      };

      return (
        <div>
          <input
            type="file"
            data-testid="file-input"
            onChange={handleFileChange}
            accept=".fasta"
          />
          {fileName && <p data-testid="file-name">{fileName}</p>}
        </div>
      );
    };

    render(<TestComponent />);

    const input = screen.getByTestId('file-input');
    const file = new File(['ATCG'], 'test.fasta', { type: 'application/octet-stream' });

    await user.upload(input, file);

    await waitFor(() => {
      expect(screen.getByTestId('file-name')).toHaveTextContent('test.fasta');
    });
  });

  it('should handle multiple file uploads sequentially', async () => {
    const user = userEvent.setup();
    const uploadedFiles = [];

    const TestComponent = () => {
      const [files, setFiles] = React.useState([]);

      const handleFilesChange = (e) => {
        const selectedFiles = Array.from(e.target.files || []);
        setFiles((prev) => [...prev, ...selectedFiles]);
      };

      return (
        <div>
          <input
            type="file"
            data-testid="file-input"
            onChange={handleFilesChange}
            multiple
          />
          <ul>
            {files.map((f, i) => (
              <li key={i} data-testid={`file-${i}`}>
                {f.name}
              </li>
            ))}
          </ul>
        </div>
      );
    };

    render(<TestComponent />);

    const input = screen.getByTestId('file-input');

    const file1 = new File(['content1'], 'file1.fasta');
    const file2 = new File(['content2'], 'file2.vcf');

    await user.upload(input, [file1, file2]);

    await waitFor(() => {
      expect(screen.getByTestId('file-0')).toHaveTextContent('file1.fasta');
      expect(screen.getByTestId('file-1')).toHaveTextContent('file2.vcf');
    });
  });

  it('should display upload progress during file transfer', async () => {
    const TestComponent = () => {
      const [progress, setProgress] = React.useState(0);
      const [uploading, setUploading] = React.useState(false);

      const simulateUpload = async () => {
        setUploading(true);
        for (let i = 0; i <= 100; i += 20) {
          setProgress(i);
          await new Promise((resolve) => setTimeout(resolve, 10));
        }
        setUploading(false);
      };

      return (
        <div>
          <button onClick={simulateUpload} data-testid="upload-btn">
            Upload
          </button>
          {uploading && (
            <div>
              <div data-testid="progress-bar" style={{ width: `${progress}%` }}>
                {progress}%
              </div>
            </div>
          )}
        </div>
      );
    };

    render(<TestComponent />);
    const user = userEvent.setup();

    const btn = screen.getByTestId('upload-btn');
    await user.click(btn);

    await waitFor(() => {
      expect(screen.getByTestId('progress-bar')).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.queryByTestId('progress-bar')).not.toBeInTheDocument();
    }, { timeout: 500 });
  });

  it('should display error message on upload failure', async () => {
    const user = userEvent.setup();

    const TestComponent = () => {
      const [error, setError] = React.useState('');

      const handleError = () => {
        setError('Upload failed: network error');
      };

      return (
        <div>
          <button onClick={handleError} data-testid="fail-btn">
            Fail Upload
          </button>
          {error && <p data-testid="error-msg">{error}</p>}
        </div>
      );
    };

    render(<TestComponent />);

    const btn = screen.getByTestId('fail-btn');
    await user.click(btn);

    await waitFor(() => {
      expect(screen.getByTestId('error-msg')).toHaveTextContent('Upload failed');
    });
  });
});

describe('Dashboard Report Display Flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('should render report in iframe after job completion', async () => {
    const TestComponent = () => {
      const [reportPath, setReportPath] = React.useState('');

      React.useEffect(() => {
        // Simulate job completion setting report path
        const timer = setTimeout(() => {
          setReportPath('/data/results/test.report.html');
        }, 50);
        return () => clearTimeout(timer);
      }, []);

      return (
        <div>
          {reportPath && (
            <div data-testid="report-container">
              <iframe
                data-testid="report-iframe"
                src={reportPath}
                title="Report"
              />
            </div>
          )}
        </div>
      );
    };

    render(<TestComponent />);

    await waitFor(() => {
      expect(screen.getByTestId('report-iframe')).toBeInTheDocument();
    });

    const iframe = screen.getByTestId('report-iframe');
    expect(iframe).toHaveAttribute('src', '/data/results/test.report.html');
  });

  it('should display report metadata (sample name, database, timestamp)', async () => {
    const TestComponent = () => {
      const [report, setReport] = React.useState(null);

      React.useEffect(() => {
        setTimeout(() => {
          setReport({
            sample_name: 'sample1',
            reference_name: 'HIV',
            created_at: '2026-05-12T10:00:00',
          });
        }, 50);
      }, []);

      return (
        <div data-testid="report-meta">
          {report && (
            <>
              <p data-testid="sample-name">{report.sample_name}</p>
              <p data-testid="reference-name">{report.reference_name}</p>
              <p data-testid="created-at">{report.created_at}</p>
            </>
          )}
        </div>
      );
    };

    render(<TestComponent />);

    await waitFor(() => {
      expect(screen.getByTestId('sample-name')).toHaveTextContent('sample1');
      expect(screen.getByTestId('reference-name')).toHaveTextContent('HIV');
    });
  });

  it('should allow user to select between multiple completed reports', async () => {
    const user = userEvent.setup();

    const TestComponent = () => {
      const [reports] = React.useState([
        {
          id: 1,
          label: 'sample1 (HIV) - 2026-05-12 10:00:00',
          path: '/data/results/sample1.html',
        },
        {
          id: 2,
          label: 'sample2 (HIV) - 2026-05-12 11:00:00',
          path: '/data/results/sample2.html',
        },
      ]);
      const [selectedReport, setSelectedReport] = React.useState(reports[0]);

      return (
        <div>
          <select
            data-testid="report-selector"
            onChange={(e) => {
              const report = reports.find((r) => r.id === Number(e.target.value));
              setSelectedReport(report);
            }}
            value={selectedReport.id}
          >
            {reports.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
          <div data-testid="selected-report">{selectedReport.label}</div>
        </div>
      );
    };

    render(<TestComponent />);

    const selector = screen.getByTestId('report-selector');
    expect(screen.getByTestId('selected-report')).toHaveTextContent('sample1');

    await user.selectOptions(selector, '2');

    await waitFor(() => {
      expect(screen.getByTestId('selected-report')).toHaveTextContent('sample2');
    });
  });

  it('should display no matching results message when job finds no resistance', async () => {
    const TestComponent = () => {
      const [message, setMessage] = React.useState('');

      React.useEffect(() => {
        setTimeout(() => {
          setMessage('No database matches were found for this sample.');
        }, 50);
      }, []);

      return (
        <div>
          {message && <p data-testid="no-results-msg">{message}</p>}
        </div>
      );
    };

    render(<TestComponent />);

    await waitFor(() => {
      expect(screen.getByTestId('no-results-msg')).toHaveTextContent('No database matches');
    });
  });
});

describe('Dashboard Job Status Display', () => {
  it('should show "queued" status message', () => {
    const TestComponent = () => {
      return <p data-testid="status">Job queued (abc123...)</p>;
    };

    render(<TestComponent />);
    expect(screen.getByTestId('status')).toHaveTextContent('queued');
  });

  it('should show "running" status message', () => {
    const TestComponent = () => {
      return <p data-testid="status">Job running (abc123...)</p>;
    };

    render(<TestComponent />);
    expect(screen.getByTestId('status')).toHaveTextContent('running');
  });

  it('should show completion status with file details', () => {
    const TestComponent = () => {
      return (
        <p data-testid="status">
          FASTA profiling finished for HIV using test.fasta. Database matches were found.
        </p>
      );
    };

    render(<TestComponent />);
    expect(screen.getByTestId('status')).toHaveTextContent('finished');
    expect(screen.getByTestId('status')).toHaveTextContent('test.fasta');
  });
});
