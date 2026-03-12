import { createContext, useContext, type ReactNode } from 'react'
import type { WipClient } from '@wip/client'

const WipClientContext = createContext<WipClient | null>(null)

export interface WipProviderProps {
  client: WipClient
  children: ReactNode
}

export function WipProvider({ client, children }: WipProviderProps) {
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
