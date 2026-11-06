import { useEffect, useRef } from 'react'

/**
 * Keeps an app origin's session fresh while the app is open.
 *
 * When WIP serves apps on origins of their own (`https://<app>.<host>`), each
 * app holds a short-lived session for its origin that the auth gateway hands
 * over by redirecting through WIP's origin. A page load renews it without a
 * trace, but an app's background `fetch` cannot follow that redirect, so an
 * expired session would make API calls fail. This hook renews the session
 * before it expires: it loads `/auth/renew` in a hidden iframe, the gateway
 * runs the handshake there, and `/auth/renewed` reports back with
 * `postMessage`.
 *
 * - Renewed: the next renew is scheduled at half the remaining lifetime.
 * - The page is not on an app origin (apps served under `/apps/<name>`):
 *   nothing to renew, so it stops.
 * - The WIP login has ended (logout elsewhere, or expiry): `onSessionLost`
 *   runs; by default the page reloads, which signs in again.
 * - No answer (no gateway, e.g. a dev server): it tries again later and never
 *   reloads the page.
 */
export interface AppSessionOptions {
  /** Renew at least this often (ms). Default: 5 minutes. */
  intervalMs?: number
  /** How long to wait for the gateway's answer (ms). Default: 15 seconds. */
  timeoutMs?: number
  /** The WIP login has ended. Default: reload the page. */
  onSessionLost?: (reason: string) => void
}

export const APP_SESSION_RENEW_PATH = '/auth/renew'
export const APP_SESSION_MESSAGE = 'wip-app-session'

interface RenewMessage {
  type: string
  ok: boolean
  expires_in: number
  reason: string
}

function isRenewMessage(data: unknown): data is RenewMessage {
  return (
    typeof data === 'object' &&
    data !== null &&
    (data as { type?: unknown }).type === APP_SESSION_MESSAGE
  )
}

export function useAppSessionRenewal(options: AppSessionOptions | false = {}): void {
  // Options are read through a ref, so an app passing a new object on every
  // render does not restart the schedule.
  const optionsRef = useRef(options)
  optionsRef.current = options
  const enabled = options !== false

  useEffect(() => {
    if (!enabled || typeof window === 'undefined' || typeof document === 'undefined') return
    const opts = () => (optionsRef.current === false ? {} : optionsRef.current)
    const interval = () => opts().intervalMs ?? 5 * 60_000

    let stopped = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let pending: { frame: HTMLIFrameElement; timeout: ReturnType<typeof setTimeout> } | undefined
    let lastSuccess = Date.now()

    const schedule = (ms: number) => {
      if (stopped) return
      clearTimeout(timer)
      timer = setTimeout(renew, ms)
    }

    const finish = () => {
      if (!pending) return
      clearTimeout(pending.timeout)
      pending.frame.remove()
      pending = undefined
    }

    function renew() {
      if (stopped || pending) return
      const frame = document.createElement('iframe')
      frame.style.display = 'none'
      frame.setAttribute('aria-hidden', 'true')
      frame.src = APP_SESSION_RENEW_PATH
      const timeout = setTimeout(() => {
        finish()
        schedule(interval())
      }, opts().timeoutMs ?? 15_000)
      pending = { frame, timeout }
      document.body.appendChild(frame)
    }

    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || !isRenewMessage(event.data)) return
      if (!pending) return
      finish()
      const { ok, expires_in: expiresIn, reason } = event.data
      if (ok && reason === 'not_an_app_origin') {
        stopped = true
        return
      }
      if (ok) {
        lastSuccess = Date.now()
        const half = expiresIn > 0 ? (expiresIn * 1000) / 2 : interval()
        schedule(Math.max(Math.min(interval(), half), 10_000))
        return
      }
      stopped = true
      const lost = opts().onSessionLost
      if (lost) lost(reason)
      else window.location.reload()
    }

    // Timers are throttled in a hidden tab: renew on return when overdue.
    const onVisible = () => {
      if (document.visibilityState === 'visible' && Date.now() - lastSuccess >= interval()) {
        renew()
      }
    }

    window.addEventListener('message', onMessage)
    document.addEventListener('visibilitychange', onVisible)
    schedule(interval())
    return () => {
      stopped = true
      clearTimeout(timer)
      finish()
      window.removeEventListener('message', onMessage)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [enabled])
}
