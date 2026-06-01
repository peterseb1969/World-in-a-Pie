import { BrowserRouter, Routes, Route } from 'react-router-dom'
import HomePage from './pages/HomePage'
import AskBar from './components/AskBar'

// Path-prefix-ready for k8s/ingress (CASE-370): without this basename, React
// Router's <Link>/navigate() drop the deploy prefix and reloads 404 at the
// ingress. Net-zero for local dev — BASE_URL is '/' → basename '/'.
const BASENAME = (import.meta.env.BASE_URL || '/').replace(/\/$/, '') || '/'

export default function App() {
  return (
    <BrowserRouter basename={BASENAME}>
      <Routes>
        <Route path="/" element={<HomePage />} />
      </Routes>
      <AskBar />
    </BrowserRouter>
  )
}
