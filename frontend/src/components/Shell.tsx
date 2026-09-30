import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'

const NAV = [
  { to: '/', label: 'Overview' },
  { to: '/city', label: 'Check My City' },
  { to: '/map', label: 'Live Risk Map' },
  { to: '/conditions', label: 'Conditions' },
  { to: '/history', label: 'History' },
  { to: '/alerts', label: 'Alerts' },
]

/** Fades/slides page content in on every route change (clean transition). */
function PageTransition({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const [shown, setShown] = useState(false)
  useEffect(() => {
    setShown(false)
    const t = requestAnimationFrame(() => setShown(true))
    return () => cancelAnimationFrame(t)
  }, [location.pathname])
  return (
    <div
      className={`transition-all duration-300 ease-out ${
        shown ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-1.5'
      }`}
    >
      {children}
    </div>
  )
}

export default function Shell() {
  return (
    <div className="min-h-screen bg-night-900 text-night-100 flex flex-col">
      <header className="sticky top-0 z-[900] bg-night-950/85 backdrop-blur-md border-b border-night-700">
        <div className="max-w-7xl mx-auto px-4 h-14 flex items-center justify-between gap-4">
          <Link to="/" className="flex items-center gap-2.5 group">
            <svg aria-hidden viewBox="0 0 24 24" className="h-6 w-6 text-sky-400 group-hover:text-sky-300 transition-colors">
              <path
                d="M12 2.7c3.3 4.2 6.5 8 6.5 11.6A6.5 6.5 0 0 1 12 20.8a6.5 6.5 0 0 1-6.5-6.5C5.5 10.7 8.7 6.9 12 2.7Z"
                fill="currentColor"
              />
            </svg>
            <span className="text-lg font-extrabold tracking-[0.18em] text-night-50 select-none">
              FLU<span className="text-sky-400">VORA</span>
            </span>
          </Link>
          <nav className="flex items-center gap-1 overflow-x-auto text-sm">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                className={({ isActive }) =>
                  `relative px-3 py-1.5 whitespace-nowrap transition-colors duration-200 ${
                    isActive ? 'text-night-50' : 'text-night-400 hover:text-night-100'
                  }`
                }
              >
                {({ isActive }) => (
                  <>
                    {n.label}
                    <span
                      aria-hidden
                      className={`absolute left-3 right-3 -bottom-0.5 h-0.5 rounded-full bg-sky-400 transition-all duration-300 ${
                        isActive ? 'opacity-100 scale-x-100' : 'opacity-0 scale-x-50'
                      }`}
                    />
                  </>
                )}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 py-6">
        <PageTransition key={location.pathname}>
          <Outlet />
        </PageTransition>
      </main>
      <footer className="border-t border-night-700 bg-night-950">
        <div className="max-w-7xl mx-auto px-4 py-4 text-xs text-night-500 space-y-1">
          <p className="flex items-center gap-2">
            <svg aria-hidden viewBox="0 0 24 24" className="h-3.5 w-3.5 text-sky-400/70">
              <path d="M12 2.7c3.3 4.2 6.5 8 6.5 11.6A6.5 6.5 0 0 1 12 20.8a6.5 6.5 0 0 1-6.5-6.5C5.5 10.7 8.7 6.9 12 2.7Z" fill="currentColor" />
            </svg>
            <span className="font-semibold tracking-[0.14em] text-night-400">FLUVORA</span>
            <span>— decision support only. Follow official warnings from IMD, CWC, NDMA and your local administration.</span>
          </p>
          <p>
            Data: Copernicus/ECMWF ERA5 · Copernicus GloFAS · Copernicus DEM · GDACS (EC JRC/UN) ·
            geoBoundaries · data.gov.in (key-gated).
          </p>
        </div>
      </footer>
    </div>
  )
}
