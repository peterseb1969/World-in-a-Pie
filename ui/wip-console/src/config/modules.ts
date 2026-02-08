/**
 * Optional module configuration for WIP Console
 *
 * Environment variables:
 * - VITE_REPORTING_ENABLED: Enable/disable reporting features (default: false)
 * - VITE_FILES_ENABLED: Enable/disable file storage features (default: false)
 * - VITE_INGEST_ENABLED: Enable/disable ingest features (default: false)
 *
 * These should match the WIP_MODULES setting in .env
 */

/**
 * Check if reporting module is enabled
 * When disabled, audit trail and integrity checks are hidden
 */
export const isReportingEnabled = (): boolean => {
  const enabled = import.meta.env.VITE_REPORTING_ENABLED
  return enabled === 'true' || enabled === true
}

/**
 * Check if file storage module is enabled
 * When disabled, file upload/download features are hidden
 */
export const isFilesEnabled = (): boolean => {
  const enabled = import.meta.env.VITE_FILES_ENABLED
  return enabled === 'true' || enabled === true
}

/**
 * Check if ingest module is enabled
 * When disabled, ingest features are hidden
 */
export const isIngestEnabled = (): boolean => {
  const enabled = import.meta.env.VITE_INGEST_ENABLED
  return enabled === 'true' || enabled === true
}

/**
 * Get all enabled modules for display
 */
export const getEnabledModules = (): string[] => {
  const modules: string[] = []
  if (isReportingEnabled()) modules.push('reporting')
  if (isFilesEnabled()) modules.push('files')
  if (isIngestEnabled()) modules.push('ingest')
  return modules
}
