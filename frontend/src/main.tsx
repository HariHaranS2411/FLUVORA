import React, { Suspense, lazy } from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import Shell from './components/Shell'
import Home from './pages/Home'
import './index.css'

// Route-level code splitting: heavy pages (recharts, leaflet) load on demand
// so the first paint (Home) doesn't wait for the whole app bundle.
const LiveMap = lazy(() => import('./pages/LiveMap'))
const Location = lazy(() => import('./pages/Location'))
const Alerts = lazy(() => import('./pages/Alerts'))
const CurrentConditions = lazy(() => import('./pages/CurrentConditions'))
const History = lazy(() => import('./pages/History'))
const CityCheck = lazy(() => import('./pages/CityCheck'))

function RouteFallback() {
  return (
    <div className="flex items-center justify-center gap-3 py-24 text-night-400" role="status" aria-live="polite">
      <span className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" aria-hidden />
      <span className="text-sm">Loading…</span>
    </div>
  )
}

const route = (el: React.ReactNode) => <Suspense fallback={<RouteFallback />}>{el}</Suspense>

const router = createBrowserRouter([
  {
    path: '/',
    element: <Shell />,
    children: [
      { index: true, element: <Home /> },
      { path: 'map', element: route(<LiveMap />) },
      { path: 'location/:id', element: route(<Location />) },
      { path: 'alerts', element: route(<Alerts />) },
      { path: 'conditions', element: route(<CurrentConditions />) },
      { path: 'history', element: route(<History />) },
      { path: 'city', element: route(<CityCheck />) },
    ],
  },
])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)
