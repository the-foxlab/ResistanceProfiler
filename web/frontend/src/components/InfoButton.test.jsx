import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';

import { InfoButton } from './InfoButton';
import { TourProvider, TOUR_STORAGE_KEY } from './tour/TourContext';
import { TourOverlay } from './tour/TourOverlay';

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe('InfoButton', () => {
  it('renders an accessible closed disclosure button without native title attribute', () => {
    render(<InfoButton label="Frequency info" title="Minimum allele frequency threshold." />);
    const button = screen.getByRole('button', { name: 'Frequency info' });
    expect(button).not.toHaveAttribute('title');
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(button).not.toHaveAttribute('aria-controls');
    expect(button).not.toHaveAttribute('aria-describedby');
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('supports ariaLabel and text prop aliases and omits disclosure attributes when text is empty', () => {
    const { rerender } = render(<InfoButton ariaLabel="Coverage info" text="Minimum read depth." />);
    const button = screen.getByRole('button', { name: 'Coverage info' });
    fireEvent.click(button);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Minimum read depth.');

    rerender(<InfoButton label="Empty info" title="" />);
    expect(button).not.toHaveAttribute('aria-expanded');
    fireEvent.click(button);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('toggles tooltip open and closed on click via fireEvent and userEvent', async () => {
    const user = userEvent.setup();
    render(<InfoButton label="BAM info" title="Optional alignment file." />);
    const button = screen.getByRole('button', { name: 'BAM info' });

    await user.click(button);
    const tooltip = screen.getByRole('tooltip');
    expect(tooltip).toHaveTextContent('Optional alignment file.');
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(button).toHaveAttribute('aria-controls', tooltip.id);
    expect(button).toHaveAttribute('aria-describedby', tooltip.id);

    await user.click(button);
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('tooltip')).toBeNull();

    fireEvent.click(button);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.click(button);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('opens on keyboard Tab focus, closes on Escape without leaking to document, and closes on blur', async () => {
    const user = userEvent.setup();
    const docEscapeSpy = vi.fn();
    const handleDocKey = (e) => {
      if (e.key === 'Escape') docEscapeSpy();
    };
    document.addEventListener('keydown', handleDocKey);

    try {
      render(
        <div>
          <InfoButton label="Sample info" title="Sample identifier." />
          <button type="button">Next control</button>
        </div>,
      );
      const infoBtn = screen.getByRole('button', { name: 'Sample info' });

      await user.tab();
      expect(document.activeElement).toBe(infoBtn);
      expect(screen.getByRole('tooltip')).toHaveTextContent('Sample identifier.');
      expect(infoBtn).toHaveAttribute('aria-expanded', 'true');

      // First Escape dismisses the tooltip, keeps focus on infoBtn, and stops propagation.
      await user.keyboard('{Escape}');
      expect(screen.queryByRole('tooltip')).toBeNull();
      expect(infoBtn).toHaveAttribute('aria-expanded', 'false');
      expect(document.activeElement).toBe(infoBtn);
      expect(docEscapeSpy).not.toHaveBeenCalled();

      // Re-open and tab away -> blur closes tooltip.
      fireEvent.focus(infoBtn);
      expect(screen.getByRole('tooltip')).toBeInTheDocument();
      await user.tab();
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Next control' }));
      expect(screen.queryByRole('tooltip')).toBeNull();
    } finally {
      document.removeEventListener('keydown', handleDocKey);
    }
  });

  it('opens on hover, closes on mouseLeave unless click-pinned, and closes on Escape/outside click', () => {
    render(
      <div>
        <InfoButton label="Hover info" title="Hover help." />
        <button type="button">Outside</button>
      </div>,
    );
    const button = screen.getByRole('button', { name: 'Hover info' });
    const wrap = button.closest('.input-info-wrap');

    fireEvent.mouseEnter(wrap);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.mouseLeave(wrap);
    expect(screen.queryByRole('tooltip')).toBeNull();

    // Hover open -> document Escape closes it.
    fireEvent.mouseEnter(wrap);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).toBeNull();

    // Click pins across mouseLeave; outside pointerdown closes it.
    fireEvent.click(button);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.mouseLeave(wrap);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Outside' }));
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('assigns unique tooltip IDs via useId() across multiple instances', () => {
    render(
      <div>
        <InfoButton label="First" title="First help" />
        <InfoButton label="Second" title="Second help" />
      </div>,
    );
    const first = screen.getByRole('button', { name: 'First' });
    const second = screen.getByRole('button', { name: 'Second' });
    fireEvent.click(first);
    fireEvent.click(second);

    const tooltips = screen.getAllByRole('tooltip');
    expect(tooltips).toHaveLength(2);
    expect(tooltips[0].id).not.toBe(tooltips[1].id);
    expect(first.getAttribute('aria-controls')).toBe(tooltips[0].id);
    expect(second.getAttribute('aria-controls')).toBe(tooltips[1].id);
  });

  it('prevents label-wrapped input activation when clicking inside the popover', async () => {
    const user = userEvent.setup();
    const onInputClick = vi.fn();
    render(
      <label>
        <span>
          VCF file <InfoButton label="VCF info" title="Upload a VCF file." />
        </span>
        <input type="file" onClick={onInputClick} />
      </label>,
    );

    const button = screen.getByRole('button', { name: 'VCF info' });
    await user.click(button);
    const tooltip = screen.getByRole('tooltip');
    await user.click(tooltip);

    expect(onInputClick).not.toHaveBeenCalled();
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
  });

  it('adds is-aligned-right class when wrap rect is near the viewport right edge', () => {
    const origWidth = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', { value: 400, configurable: true });
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      left: 350,
      top: 10,
      width: 16,
      height: 16,
      right: 366,
      bottom: 26,
    });

    try {
      render(<InfoButton label="Edge info" title="Right edge help." />);
      fireEvent.click(screen.getByRole('button', { name: 'Edge info' }));
      expect(screen.getByRole('tooltip')).toHaveClass('is-aligned-right');
    } finally {
      Object.defineProperty(window, 'innerWidth', { value: origWidth, configurable: true });
    }
  });

  it('closes only the hover-opened tooltip on Escape when TourOverlay coach card or active tour is open', async () => {
    const user = userEvent.setup();
    const steps = [{ id: 'a', targetSelector: '#info-host', title: 'Step A', body: 'Body A' }];
    const { container } = render(
      <MemoryRouter initialEntries={['/analysis']}>
        <TourProvider steps={steps}>
          <div id="info-host">
            <label>
              <span>
                Frequency <InfoButton label="Frequency info" title="Minimum allele frequency." />
              </span>
              <input type="number" />
            </label>
          </div>
          <TourOverlay />
        </TourProvider>
      </MemoryRouter>,
    );

    expect(container.querySelector('.tour-coach-card')).not.toBeNull();
    const input = screen.getByRole('spinbutton');
    input.focus();
    const infoBtn = screen.getByRole('button', { name: 'Frequency info' });
    await user.hover(infoBtn);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();

    // Modified Escape (e.g. Shift+Escape) does not close InfoButton or leak.
    fireEvent.keyDown(document, { key: 'Escape', shiftKey: true });
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    expect(container.querySelector('.tour-coach-card')).not.toBeNull();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(container.querySelector('.tour-coach-card')).not.toBeNull();
    expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBeNull();

    // Active tour: hover InfoButton open, press Escape -> closes tooltip only, tour stays active.
    await user.click(screen.getByRole('button', { name: /start tour/i }));
    expect(container.querySelector('.tour-overlay')).not.toBeNull();
    await user.hover(infoBtn);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();

    // Modified Escape (e.g. Shift+Escape) does not close InfoButton or active tour.
    fireEvent.keyDown(document, { key: 'Escape', shiftKey: true });
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    expect(container.querySelector('.tour-overlay')).not.toBeNull();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('tooltip')).toBeNull();
    expect(container.querySelector('.tour-overlay')).not.toBeNull();
    expect(localStorage.getItem(TOUR_STORAGE_KEY)).toBeNull();
  });

  it('resets pointerDown state after pointerdown + drag-off pointerup outside so subsequent Tab opens tooltip', async () => {
    const user = userEvent.setup();
    render(
      <div>
        <button type="button">Before</button>
        <InfoButton label="Drag info" title="Drag test help." />
        <button type="button">Outside</button>
      </div>,
    );

    const infoBtn = screen.getByRole('button', { name: 'Drag info' });
    const outside = screen.getByRole('button', { name: 'Outside' });

    fireEvent.pointerDown(infoBtn);
    fireEvent.pointerUp(outside);
    expect(screen.queryByRole('tooltip')).toBeNull();

    screen.getByRole('button', { name: 'Before' }).focus();
    await user.tab();
    expect(document.activeElement).toBe(infoBtn);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Drag test help.');
  });

  it('treats mousedown -> focus -> click without pointerdown as a pointer click and keeps tooltip open', () => {
    render(<InfoButton label="Mouse info" title="Mouse click help." />);
    const infoBtn = screen.getByRole('button', { name: 'Mouse info' });

    fireEvent.mouseDown(infoBtn);
    fireEvent.focus(infoBtn);
    fireEvent.click(infoBtn);

    expect(infoBtn).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('tooltip')).toHaveTextContent('Mouse click help.');
  });

  it('omits aria-expanded and renders no tooltip for whitespace-only title or text', () => {
    const { rerender } = render(<InfoButton label="Blank title" title="   " />);
    const button = screen.getByRole('button', { name: 'Blank title' });
    expect(button).not.toHaveAttribute('aria-expanded');
    fireEvent.click(button);
    expect(screen.queryByRole('tooltip')).toBeNull();

    rerender(<InfoButton label="Blank text" text={' \t\n '} />);
    expect(button).not.toHaveAttribute('aria-expanded');
    fireEvent.focus(button);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('clamps narrow-viewport popovers with --popover-shift instead of overflowing off the left edge', () => {
    const origWidth = window.innerWidth;
    const rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect');

    try {
      Object.defineProperty(window, 'innerWidth', { value: 320, configurable: true });
      rectSpy.mockReturnValue({ left: 100, top: 10, width: 16, height: 16, right: 116, bottom: 26 });

      const { unmount } = render(<InfoButton label="Narrow info" title="Narrow help." />);
      fireEvent.click(screen.getByRole('button', { name: 'Narrow info' }));
      const narrowTooltip = screen.getByRole('tooltip');
      expect(narrowTooltip).not.toHaveClass('is-aligned-right');
      expect(narrowTooltip.style.getPropertyValue('--popover-shift')).toBe('-80px');
      unmount();

      Object.defineProperty(window, 'innerWidth', { value: 1024, configurable: true });
      rectSpy.mockReturnValue({ left: 950, top: 10, width: 16, height: 16, right: 966, bottom: 26 });

      render(<InfoButton label="Desktop edge info" title="Desktop edge help." />);
      fireEvent.click(screen.getByRole('button', { name: 'Desktop edge info' }));
      const desktopTooltip = screen.getByRole('tooltip');
      expect(desktopTooltip).toHaveClass('is-aligned-right');
      expect(desktopTooltip.style.getPropertyValue('--popover-shift')).toBe('');
    } finally {
      Object.defineProperty(window, 'innerWidth', { value: origWidth, configurable: true });
    }
  });
});
