import type { CSSProperties, ReactElement } from 'react'

export interface WipFooterProps {
  /** When set, prepends "<appName> · " to the attribution text. */
  appName?: string
  /** Layout-only override merged onto the wrapper <footer>. */
  className?: string
  /** v1 ships 'compact' only. 'full' is reserved for v1.5. */
  variant?: 'compact' | 'full'
  /** Optional inline style override for the wrapper. */
  style?: CSSProperties
}

const WRAPPER_BASE = 'mt-12 border-t border-gray-200 py-4'
const INNER = 'mx-auto flex max-w-6xl items-center justify-center gap-2 px-4 text-xs text-text-muted'
const LOGO_CLASS = 'h-4 w-auto'

function joinClassNames(base: string, override?: string): string {
  return override ? `${base} ${override}` : base
}

// World-in-a-pie geometric mark — flat SVG referent designed for footer
// scale (h-4). Per CASE-314 (FRanC's response, v3 iteration): the world is
// the upper 217° pie body cut at y=82; the dish sits at y=86–92, separated
// by a 4-unit gap, which reads as "world tucked into pie". Inline (not
// asset-loaded) so consumers don't need to copy anything to their app's
// public/. The full-illustration WIP_logo_blue_small.png is still the
// canonical hero/splash mark; this footer mark is the small-scale referent.
export function WipFooter({
  appName,
  className,
  variant = 'compact',
  style,
}: WipFooterProps): ReactElement {
  const text = appName ? `${appName} · Built on WIP` : 'Built on WIP'

  return (
    <footer className={joinClassNames(WRAPPER_BASE, className)} style={style} data-wip-footer-variant={variant}>
      <div className={INNER}>
        <svg
          viewBox="0 0 100 100"
          className={LOGO_CLASS}
          aria-hidden="true"
          focusable="false"
        >
          <path d="M50 50 L90 50 A40 40 0 0 1 74 82 L26 82 A40 40 0 0 1 50 10 Z" fill="#2B579A" />
          <rect x="5" y="86" width="90" height="6" rx="3" fill="#2B579A" />
        </svg>
        <span>{text}</span>
      </div>
    </footer>
  )
}
