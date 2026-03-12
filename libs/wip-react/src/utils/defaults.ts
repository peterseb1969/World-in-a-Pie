/** Default stale times for different entity types (milliseconds). */
export const STALE_TIMES = {
  /** Terminologies change rarely */
  terminologies: 5 * 60 * 1000,
  /** Terms change rarely */
  terms: 5 * 60 * 1000,
  /** Templates change rarely */
  templates: 5 * 60 * 1000,
  /** Documents change more frequently */
  documents: 30 * 1000,
  /** Files rarely change after upload */
  files: 10 * 60 * 1000,
  /** Registry data is relatively stable */
  registry: 5 * 60 * 1000,
  /** Reporting data may be slightly stale */
  reporting: 60 * 1000,
} as const
