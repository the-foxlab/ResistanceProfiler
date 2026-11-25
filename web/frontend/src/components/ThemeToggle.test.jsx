import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { ThemeToggle } from './ThemeToggle';

describe('ThemeToggle', () => {
  it('renders a radiogroup with roving tabIndex on the active radio', () => {
    render(<ThemeToggle preference="light" onChange={vi.fn()} />);

    const group = screen.getByRole('radiogroup', { name: 'Colour theme' });
    expect(group).toBeInTheDocument();

    const system = screen.getByRole('radio', { name: 'System' });
    const light = screen.getByRole('radio', { name: 'Light' });
    const dark = screen.getByRole('radio', { name: 'Dark' });

    expect(system).toHaveAttribute('aria-checked', 'false');
    expect(system).toHaveAttribute('tabindex', '-1');
    expect(system).not.toHaveClass('active');

    expect(light).toHaveAttribute('aria-checked', 'true');
    expect(light).toHaveAttribute('tabindex', '0');
    expect(light).toHaveClass('active');

    expect(dark).toHaveAttribute('aria-checked', 'false');
    expect(dark).toHaveAttribute('tabindex', '-1');
    expect(dark).not.toHaveClass('active');
  });

  it('falls back to tabIndex=0 on the first radio when preference is unrecognized or missing', () => {
    const { rerender } = render(<ThemeToggle preference={undefined} onChange={vi.fn()} />);
    expect(screen.getByRole('radio', { name: 'System' })).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('radio', { name: 'Light' })).toHaveAttribute('tabindex', '-1');
    expect(screen.getByRole('radio', { name: 'Dark' })).toHaveAttribute('tabindex', '-1');

    rerender(<ThemeToggle preference="invalid" onChange={vi.fn()} />);
    expect(screen.getByRole('radio', { name: 'System' })).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('radio', { name: 'System' })).toHaveAttribute('aria-checked', 'false');
  });

  it('invokes onChange with the option value on click', () => {
    const onChange = vi.fn();
    render(<ThemeToggle preference="system" onChange={onChange} />);

    fireEvent.click(screen.getByRole('radio', { name: 'Light' }));
    expect(onChange).toHaveBeenCalledWith('light');
    fireEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    expect(onChange).toHaveBeenCalledWith('dark');
    fireEvent.click(screen.getByRole('radio', { name: 'System' }));
    expect(onChange).toHaveBeenCalledWith('system');
  });

  it('navigates and wraps around on ArrowRight/ArrowDown and ArrowLeft/ArrowUp', () => {
    const onChange = vi.fn();
    render(<ThemeToggle preference="system" onChange={onChange} />);

    const system = screen.getByRole('radio', { name: 'System' });
    const light = screen.getByRole('radio', { name: 'Light' });
    const dark = screen.getByRole('radio', { name: 'Dark' });

    system.focus();
    fireEvent.keyDown(system, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(light);
    expect(onChange).toHaveBeenLastCalledWith('light');

    fireEvent.keyDown(light, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(dark);
    expect(onChange).toHaveBeenLastCalledWith('dark');

    fireEvent.keyDown(dark, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(system);
    expect(onChange).toHaveBeenLastCalledWith('system');

    fireEvent.keyDown(system, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(dark);
    expect(onChange).toHaveBeenLastCalledWith('dark');

    fireEvent.keyDown(dark, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(light);
    expect(onChange).toHaveBeenLastCalledWith('light');
  });

  it('ignores non-arrow keys and modified arrow keystrokes without calling preventDefault', () => {
    const onChange = vi.fn();
    render(<ThemeToggle preference="system" onChange={onChange} />);
    const system = screen.getByRole('radio', { name: 'System' });
    system.focus();

    for (const key of ['Tab', 'Enter', ' ', 'a']) {
      const ev = new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
      system.dispatchEvent(ev);
      expect(ev.defaultPrevented).toBe(false);
    }

    for (const mod of ['altKey', 'ctrlKey', 'metaKey', 'shiftKey']) {
      const ev = new KeyboardEvent('keydown', { key: 'ArrowRight', [mod]: true, bubbles: true, cancelable: true });
      system.dispatchEvent(ev);
      expect(ev.defaultPrevented).toBe(false);
    }

    expect(onChange).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(system);
  });

  it('updates aria-checked, active class, and roving tabIndex in a controlled stateful parent', () => {
    function ControlledHarness() {
      const [pref, setPref] = useState('system');
      return <ThemeToggle preference={pref} onChange={setPref} />;
    }

    render(<ControlledHarness />);
    const system = screen.getByRole('radio', { name: 'System' });
    const light = screen.getByRole('radio', { name: 'Light' });
    const dark = screen.getByRole('radio', { name: 'Dark' });

    system.focus();
    fireEvent.keyDown(system, { key: 'ArrowRight' });
    expect(document.activeElement).toBe(light);
    expect(light).toHaveAttribute('aria-checked', 'true');
    expect(light).toHaveAttribute('tabindex', '0');
    expect(light).toHaveClass('active');
    expect(system).toHaveAttribute('tabindex', '-1');

    fireEvent.keyDown(light, { key: 'ArrowLeft' });
    fireEvent.keyDown(system, { key: 'ArrowLeft' });
    expect(document.activeElement).toBe(dark);
    expect(dark).toHaveAttribute('aria-checked', 'true');
    expect(dark).toHaveAttribute('tabindex', '0');
    expect(dark).toHaveClass('active');
    expect(light).toHaveAttribute('tabindex', '-1');
  });

  it('exposes title attributes, aria-hidden icons, and safe no-op when onChange is omitted', () => {
    const { container } = render(<ThemeToggle preference="system" />);
    const system = screen.getByRole('radio', { name: 'System' });
    const light = screen.getByRole('radio', { name: 'Light' });
    const dark = screen.getByRole('radio', { name: 'Dark' });

    expect(system).toHaveAttribute('title', 'Follow system preference');
    expect(light).toHaveAttribute('title', 'Always light');
    expect(dark).toHaveAttribute('title', 'Always dark');

    const icons = container.querySelectorAll('.theme-toggle-icon');
    expect(icons).toHaveLength(3);
    for (const icon of icons) {
      expect(icon).toHaveAttribute('aria-hidden', 'true');
    }

    expect(() => {
      fireEvent.click(light);
      system.focus();
      fireEvent.keyDown(system, { key: 'ArrowRight' });
    }).not.toThrow();
  });
});
