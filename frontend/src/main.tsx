import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { attachPointerSpotlight, prefersReducedMotion } from './lib/motion'
import './index.css'

const container = document.getElementById('root')
if (!container) throw new Error('Root container missing in index.html')

// Opt the document into scroll reveals only when the observer exists and the OS allows motion.
if (typeof IntersectionObserver !== 'undefined' && !prefersReducedMotion()) {
  document.documentElement.classList.add('motion-on')
}
attachPointerSpotlight()

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
