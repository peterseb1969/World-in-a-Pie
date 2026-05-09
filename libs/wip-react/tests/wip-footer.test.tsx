import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { WipFooter } from '../src/WipFooter'

describe('WipFooter (CASE-308 + CASE-314)', () => {
  it('renders the default attribution with the inline SVG mark', () => {
    const { container, getByText } = render(<WipFooter />)

    const footer = container.querySelector('footer')
    expect(footer).not.toBeNull()
    expect(footer!.className).toContain('mt-12')
    expect(footer!.className).toContain('border-t')

    // CASE-314: mark is now an inline SVG (not an <img>) — decorative
    // (aria-hidden), so the adjacent text "Built on WIP" provides the
    // accessible label. Structural check rather than getByAltText.
    const svg = container.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(svg!.getAttribute('aria-hidden')).toBe('true')
    expect(svg!.getAttribute('viewBox')).toBe('0 0 100 100')
    expect(svg!.getAttribute('class')).toContain('h-4')

    // CASE-321: intrinsic width/height attributes are required so the
    // SVG renders at the correct size even when Tailwind's JIT hasn't
    // compiled the h-4/w-auto utilities (which happens by default in
    // every consumer because node_modules/@wip/react isn't in their
    // tailwind content paths). Without these attrs, SVG with viewBox
    // and no dimensions fills the flex parent — page-dominating logo.
    expect(svg!.getAttribute('width')).toBe('16')
    expect(svg!.getAttribute('height')).toBe('16')

    // World-in-a-pie geometry: pie body path + dish rect both render
    expect(svg!.querySelector('path')).not.toBeNull()
    expect(svg!.querySelector('rect')).not.toBeNull()

    expect(getByText('Built on WIP')).not.toBeNull()
  })

  it('prepends appName to the attribution when provided', () => {
    const { getByText } = render(<WipFooter appName="ClinTrial" />)
    expect(getByText('ClinTrial · Built on WIP')).not.toBeNull()
  })

  it('merges className onto the wrapper without dropping the canonical classes', () => {
    const { container } = render(<WipFooter className="mt-20" />)
    const footer = container.querySelector('footer')!
    expect(footer.className).toContain('mt-20')
    expect(footer.className).toContain('border-t')
  })

  it('records the variant on the wrapper for future v1.5 styling hooks', () => {
    const { container } = render(<WipFooter />)
    expect(container.querySelector('footer')!.getAttribute('data-wip-footer-variant')).toBe('compact')
  })

  it('uses a semantic <footer> element for accessibility', () => {
    const { container } = render(<WipFooter />)
    expect(container.querySelector('footer')).not.toBeNull()
  })
})
