import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { Spinner } from './Spinner';

describe('Spinner Component', () => {
  it('should render SVG spinner element', () => {
    const { container } = render(<Spinner />);
    const svg = container.querySelector('svg');

    expect(svg).not.toBeNull();
    expect(svg).toHaveClass('spinner');
  });

  it('should have proper SVG structure for spinner animation', () => {
    const { container } = render(<Spinner />);
    const circles = container.querySelectorAll('circle');

    expect(circles.length).toBe(2);
    expect(circles[0]).toHaveAttribute('cx', '25');
    expect(circles[0]).toHaveAttribute('cy', '25');
    expect(circles[0]).toHaveAttribute('r', '20');
  });
});
