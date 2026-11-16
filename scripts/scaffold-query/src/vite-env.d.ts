/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Build timestamp baked at image-build time via `--build-arg`.
   * 'dev' (or unset) for local/unstamped builds — the UI hides the stamp then.
   */
  readonly VITE_BUILD_STAMP?: string
  /** Short git SHA baked at image-build time via `--build-arg`. */
  readonly VITE_BUILD_SHA?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
