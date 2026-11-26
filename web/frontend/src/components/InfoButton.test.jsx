import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { InfoButton } from './InfoButton';

describe('InfoButton', () => {
  it('renders an accessible button and opens/closes tooltip on click, hover, focus, outside click, and Escape', () => {
    render(
      <div>
        <InfoButton label="Minimum AF info" title="Filters variants below this allele frequency." />
        <button type="button">Outside</button>
      </div>,
    );

    const btn = screen.getByRole('button', { name: 'Minimum AF info' });
    expect(btn).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

    // Hover open & close
    fireEvent.mouseEnter(btn.parentElement);
    const tip = screen.getByRole('tooltip');
    expect(tip).toHaveTextContent('Filters variants below this allele frequency.');
    expect(btn).toHaveAttribute('aria-describedby', tip.id);
    fireEvent.mouseLeave(btn.parentElement);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

    // Keyboard focus open & blur close
    fireEvent.focus(btn);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.blur(btn, { relatedTarget: screen.getByRole('button', { name: 'Outside' }) });
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

    // Pointer click toggles open and persists past mouseLeave
    fireEvent.pointerDown(btn);
    fireEvent.focus(btn);
    fireEvent.click(btn);
    fireEvent.mouseLeave(btn.parentElement);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();

    // Escape closes in capture phase without leaking to outer Escape listeners
    const outerKeyDown = vi.fn();
    document.addEventListener('keydown', outerKeyDown);
    fireEvent.keyDown(document, { key: 'Escape' });
    document.removeEventListener('keydown', outerKeyDown);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    expect(outerKeyDown).not.toHaveBeenCalled();

    // Outside pointerdown closes
    fireEvent.click(btn);
    expect(screen.getByRole('tooltip')).toBeInTheDocument();
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Outside' }));
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });

  it('aligns popover right when button is near the right viewport edge', () => {
    const origWidth = window.innerWidth;
    Object.defineProperty(window, 'innerWidth', { value: 400, configurable: true });
    render(<InfoButton label="Edge info" title="Edge tooltip" />);
    const wrap = screen.getByRole('button', { name: 'Edge info' }).parentElement;
    vi.spyOn(wrap, 'getBoundingClientRect').mockReturnValue({
      left: 360,
      right: 380,
      top: 20,
      bottom: 40,
      width: 20,
      height: 20,
    });

    fireEvent.click(screen.getByRole('button', { name: 'Edge info' }));
    expect(screen.getByRole('tooltip')).toHaveClass('is-aligned-right');
    Object.defineProperty(window, 'innerWidth', { value: origWidth, configurable: true });
  });

  it('does not open an empty tooltip when title is blank', () => {
    render(<InfoButton label="Empty info" title="   " />);
    const btn = screen.getByRole('button', { name: 'Empty info' });
    fireEvent.click(btn);
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  });
});
