import type { ReactNode } from 'react'
import { RISK_COLORS } from '../types'

export function RiskBadge({ level }: { level: string | null }) {
  const lvl = level ?? 'DATA_UNAVAILABLE'
  const c = RISK_COLORS[lvl] ?? '#64748b'
  // Tinted chip with a colored edge — calmer than a solid fill, and the
  // colored dot keeps the mapping to the map legend obvious.
  return (
    <span
      className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-semibold"
      style={{ backgroundColor: `${c}26`, color: c, border: `1px solid ${c}66` }}
    >
      <span aria-hidden className="inline-block h-1.5 w-1.5 rounded-full" style={{ backgroundColor: c }} />
      {lvl.replace('_', ' ')}
    </span>
  )
}

export function Freshness({ status }: { status: string }) {
  const map: Record<string, [string, string]> = {
    fresh: ['🟢', 'text-green-400'],
    stale: ['🟡', 'text-yellow-400'],
    unavailable: ['🔴', 'text-red-400'],
  }
  const [dot, cls] = map[status] ?? ['⚪', 'text-night-400']
  return (
    <span className={`text-xs ${cls}`}>
      {dot} {status.charAt(0).toUpperCase() + status.slice(1)}
    </span>
  )
}

export function NA({ children = 'N/A' }: { children?: ReactNode }) {
  return <span className="text-night-400 italic">{children}</span>
}

export function fmtProb(p: number | null | undefined): string {
  if (p == null) return 'N/A'
  const pct = p * 100
  if (pct > 0 && pct < 1) return pct.toFixed(1) + '%'
  return Math.round(pct) + '%'
}

export function Card({ title, children, className = '' }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section
      className={`bg-night-800 rounded-xl shadow-sm border border-night-600 p-4
                  transition-all duration-300 hover:border-night-500 hover:shadow-lg hover:shadow-black/20
                  hover:-translate-y-0.5 ${className}`}
    >
      {title && (
        <h2 className="flex items-center gap-2 text-sm font-semibold text-night-300 uppercase tracking-wide mb-3">
          <span aria-hidden className="inline-block h-3.5 w-1 rounded-full bg-gradient-to-b from-sky-400 to-cyan-500" />
          {title}
        </h2>
      )}
      {children}
    </section>
  )
}
