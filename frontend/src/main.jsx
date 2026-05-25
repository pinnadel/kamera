import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

// Apply saved UI scale before first render to prevent layout flash.
document.documentElement.dataset.uiScale = localStorage.getItem('pca.uiScale') || 'M'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
