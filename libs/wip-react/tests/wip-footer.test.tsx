import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { WipFooter } from '../src/WipFooter'

describe('WipFooter (CASE-308)', () => {
  it('renders the default attribution with the embedded logo', () => {
    const { container, getByAltText, getByText } = render(<WipFooter />)

    const footer = container.querySelector('footer')
    expect(footer).not.toBeNull()
    expect(footer!.className).toContain('mt-12')
    expect(footer!.className).toContain('border-t')

    const logo = getByAltText('WIP')
    expect(logo.tagName).toBe('IMG')
    expect((logo as HTMLImageElement).src).toMatch(/^data:image\/png;base64,/)
    expect(logo.className).toContain('h-4')
    expect(logo.className).toContain('opacity-70')

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
