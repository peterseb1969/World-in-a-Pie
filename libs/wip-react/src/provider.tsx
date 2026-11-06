import { createContext, useContext, type ReactNode } from 'react'
import type { WipClient } from '@wip/client'
import { useAppSessionRenewal, type AppSessionOptions } from './app-session.js'

const WipClientContext = createContext<WipClient | null>(null)

export interface WipProviderProps {
  client: WipClient
  children: ReactNode
  /**
   * Keep the app origin's session fresh while the app is open (see
   * useAppSessionRenewal). On by default; harmless where apps are served
   * under /apps/<name> (it stops after one check). `false` turns it off.
   */
  appSession?: AppSessionOptions | false
}

export function WipProvider({ client, children, appSession = {} }: WipProviderProps) {
  useAppSessionRenewal(appSession)
  return (
    <WipClientContext.Provider value={client}>
      {children}
    </WipClientContext.Provider>
  )
}

export function useWipClient(): WipClient {
  const client = useContext(WipClientContext)
  if (!client) {
    throw new Error('useWipClient must be used within a <WipProvider>')
  }
  return client
}
