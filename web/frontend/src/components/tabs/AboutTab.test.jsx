import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

import { AboutTab } from './AboutTab';

describe('AboutTab tour button', () => {
  beforeEach(() => {
    // AboutTab imports FRONTEND_CONFIG and assets; ensure no network calls.
  });

  it('renders a "Take a tour" button next to "Start analysis"', () => {
    render(<AboutTab setActiveMode={() => {}} onStartTour={() => {}} />);
    const startButton = screen.getByRole('button', { name: /start analysis/i });
    const tourButton = screen.getByRole('button', { name: /take a tour/i });
    expect(startButton).toBeInTheDocument();
    expect(tourButton).toBeInTheDocument();
    // The tour button must come immediately after the start button (both in .about-hero-actions).
    const actions = startButton.closest('.about-hero-actions');
    expect(actions).not.toBeNull();
    const buttons = actions.querySelectorAll('button');
    expect(buttons.length).toBeGreaterThanOrEqual(2);
    expect(buttons[0]).toBe(startButton);
    expect(buttons[1]).toBe(tourButton);
  });

  it('calls onStartTour when clicked', () => {
    const onStartTour = vi.fn();
    render(<AboutTab setActiveMode={() => {}} onStartTour={onStartTour} />);
    fireEvent.click(screen.getByRole('button', { name: /take a tour/i }));
    expect(onStartTour).toHaveBeenCalledTimes(1);
  });

  it('does not crash when onStartTour is not provided', () => {
    render(<AboutTab setActiveMode={() => {}} />);
    expect(() => fireEvent.click(screen.getByRole('button', { name: /take a tour/i }))).not.toThrow();
  });
});
