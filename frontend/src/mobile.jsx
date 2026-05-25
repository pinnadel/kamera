import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import './mobile/mobile.css'
import { MobileApp } from './mobile/MobileApp'

// Apply UI scale (S/M/L) so mobile honors the same accessibility setting as
// desktop. Mobile defaults to M (matches desktop default) — touch users can
// switch to L from Settings.
document.documentElement.dataset.uiScale = localStorage.getItem('pca.uiScale') || 'M'
document.documentElement.lang = 'en'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <MobileApp />
  </StrictMode>,
)
