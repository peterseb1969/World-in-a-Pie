import { BrowserRouter, Routes, Route } from 'react-router-dom'
import HomePage from './pages/HomePage'
import SettingsPage from './pages/SettingsPage'
import AskBar from './components/AskBar'

// Path-prefix-ready for k8s/ingress (CASE-370): without this basename, React
// Router's <Link>/navigate() drop the deploy prefix and reloads 404 at the
// ingress. Net-zero for local dev — BASE_URL is '/' → basename '/'.
const BASENAME = (import.meta.env.BASE_URL || '/').replace(/\/$/, '') || '/'

// CASE-472: surface which build is running. VITE_BUILD_STAMP / VITE_BUILD_SHA
// are baked at image-build time via the Dockerfile's `--build-arg` (see
// Dockerfile + .env.example). They are 'dev'/undefined for local builds, in
// which case the stamp is hidden. The scaffold has no @wip/react dependency,
// so this is a minimal inline element rather than <WipFooter buildStamp=…>;
// React apps that already use @wip/react should pass the same env values to
// WipFooter instead.
function BuildStamp() {
  const parts = [
    import.meta.env.VITE_BUILD_STAMP,
    import.meta.env.VITE_BUILD_SHA,
  ].filter((v): v is string => Boolean(v) && v !== 'dev')
  if (parts.length === 0) return null
  return (
    <div
      className="fixed bottom-2 left-3 z-40 select-none text-xs text-gray-400"
      title="Build of the running image"
      data-build-stamp
    >
      {parts.join(' · ')}
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter basename={BASENAME}>
      <Routes>
        <Route path="/" element={<HomePage />} />
        {/* Admin-only runtime config — set/rotate the Anthropic key (CASE-509).
            The server gates /api/config with requireAdmin; non-admins see a
            "forbidden" message rather than the form. */}
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
      <AskBar />
      <BuildStamp />
    </BrowserRouter>
  )
}
