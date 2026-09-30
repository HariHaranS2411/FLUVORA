/**
 * Animated chart helpers for recharts 2.x.
 *
 * IMPORTANT recharts 2.x constraint (verified against node_modules): a chart's
 * render() only renders children that are
 *   - real recharts components (matched by displayName), or
 *   - raw SVG elements (lower-case intrinsic tags like <defs>).
 * A plain function component placed inside <BarChart>/<LineChart> is silently
 * dropped. That is why the helpers below are FACTORIES that return elements
 * (chartGradients(), softGrid(), fancyTooltip(), glowBar(), glowArea(),
 * glowLine()) instead of React components — the returned elements are what
 * recharts knows how to render.
 *
 * Visual layer only — every value shown is the real one recharts received;
 * these helpers just make the presentation smoother and more readable:
 *  - Bars rise from the baseline on mount / data change (recharts animation).
 *  - Lines draw left→right on mount (recharts stroke-dash animation).
 *  - Gradient fills + a soft glow on hover draw the eye to the data.
 *  - A custom tooltip replaces the browser-default white box.
 */
import { useState } from 'react'
import type { ReactElement, ReactNode } from 'react'
import { Area, Bar, CartesianGrid, Line, Tooltip } from 'recharts'

export const GRID_COLOR = '#1c2a45'
export const AXIS_COLOR = '#5f7195'
export const AXIS_TICK = { fill: '#8395b8', fontSize: 11 }

/** Tracks whether the pointer is over the chart area. */
export function useChartHover(): [boolean, { onMouseEnter: () => void; onMouseLeave: () => void }] {
  const [hovered, setHovered] = useState(false)
  return [
    hovered,
    { onMouseEnter: () => setHovered(true), onMouseLeave: () => setHovered(false) },
  ]
}

/**
 * Wrapper that makes the chart inside it rise toward the pointer:
 * a gentle bottom-anchored vertical stretch + glow while hovered.
 * Renders an explicit full-height box so recharts' ResponsiveContainer
 * (which needs a sized parent) keeps working inside it.
 * (Regular <div> outside the chart SVG — a real component is fine here.)
 */
export function RiseOnHover({ hovered, children }: { hovered: boolean; children: ReactNode }) {
  return (
    <div
      style={{
        width: '100%',
        height: '100%',
        transform: hovered ? 'scaleY(1.05)' : 'scaleY(1)',
        transformOrigin: 'center bottom',
        transition: 'transform 350ms cubic-bezier(0.22, 1, 0.36, 1), filter 350ms ease',
        filter: hovered ? 'brightness(1.18) saturate(1.15)' : 'none',
      }}
    >
      {children}
    </div>
  )
}

export const grad = (color: string) => `url(#grad-${color.slice(1)})`

/**
 * Raw <defs> block with one vertical gradient per color — recharts passes raw
 * SVG children through untouched. Place FIRST inside the chart:
 *   {chartGradients(['#f97316'])}  then  fill={grad('#f97316')}
 * Pass area=true for the soft fade used under lines.
 */
export function chartGradients(colors: string[], area = false): ReactElement {
  return (
    <defs>
      {colors.map((c) => (
        <linearGradient key={c} id={`grad-${c.slice(1)}`} x1="0" y1="0" x2="0" y2="1">
          {area ? (
            <>
              <stop offset="0%" stopColor={c} stopOpacity={0.45} />
              <stop offset="100%" stopColor={c} stopOpacity={0.02} />
            </>
          ) : (
            <>
              <stop offset="0%" stopColor={c} stopOpacity={1} />
              <stop offset="55%" stopColor={c} stopOpacity={0.85} />
              <stop offset="100%" stopColor={c} stopOpacity={0.45} />
            </>
          )}
        </linearGradient>
      ))}
    </defs>
  )
}

/** Real recharts <Tooltip> wrapping a custom dark card with glowing value dots. */
export function fancyTooltip(unit?: string): ReactElement {
  return (
    <Tooltip
      cursor={{ fill: 'rgba(56,189,248,0.08)' }}
      isAnimationActive={false}
      content={<TooltipCard unit={unit} />}
    />
  )
}

function TooltipCard(props: {
  active?: boolean
  payload?: Array<{ name?: string; value?: number | string; color?: string; dataKey?: string }>
  label?: string | number
  unit?: string
}) {
  const { active, payload, label, unit } = props
  if (!active || !payload || payload.length === 0) return null
  return (
    <div
      className="rounded-lg border border-night-500 bg-night-950/95 px-3 py-2 text-xs shadow-xl"
      style={{ backdropFilter: 'blur(4px)' }}
    >
      {label != null && label !== '' && (
        <div className="mb-1 font-semibold text-night-100">{label}</div>
      )}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2 text-night-200">
          <span
            aria-hidden
            className="inline-block h-2 w-2 rounded-full"
            style={{ background: p.color ?? '#38bdf8', boxShadow: `0 0 6px ${p.color ?? '#38bdf8'}` }}
          />
          <span>{p.name ?? p.dataKey}:</span>
          <b className="text-white">{typeof p.value === 'number' ? p.value.toLocaleString() : p.value}{unit ?? ''}</b>
        </div>
      ))}
    </div>
  )
}

/** Animated gradient bar with hover highlight (element factory). */
export function glowBar(opts: { dataKey: string; name: string; color: string; hovered: boolean }): ReactElement {
  const { dataKey, name, color, hovered } = opts
  return (
    <Bar
      dataKey={dataKey}
      name={name}
      fill={grad(color)}
      radius={[4, 4, 0, 0]}
      isAnimationActive
      animationDuration={900}
      animationEasing="ease-out"
      activeBar={{ fill: grad(color), stroke: '#f8fafc', strokeWidth: 1 }}
      style={{
        transition: 'filter 250ms ease',
        filter: hovered ? `brightness(1.2) drop-shadow(0 0 6px ${color})` : 'none',
      }}
    />
  )
}

/** Gradient area that fades to transparent — rises on mount (element factory). */
export function glowArea(opts: { dataKey: string; name: string; color: string }): ReactElement {
  const { dataKey, name, color } = opts
  return (
    <Area
      type="monotone"
      dataKey={dataKey}
      name={name}
      fill={grad(color)}
      strokeWidth={0}
      legendType="none"
      isAnimationActive
      animationDuration={900}
      animationEasing="ease-out"
    />
  )
}

/**
 * Line that draws left→right on mount with a soft glow (element factory).
 * Pair with glowArea() for the gradient fill underneath.
 */
export function glowLine(opts: { dataKey: string; name: string; color: string; hovered: boolean }): ReactElement {
  const { dataKey, name, color, hovered } = opts
  return (
    <Line
      type="monotone"
      dataKey={dataKey}
      name={name}
      stroke={color}
      strokeWidth={hovered ? 3.2 : 2.2}
      dot={false}
      activeDot={{ r: 4, fill: color, stroke: '#f8fafc', strokeWidth: 1.5 }}
      isAnimationActive
      animationDuration={1100}
      animationEasing="ease-out"
      style={{
        transition: 'stroke-width 200ms ease, filter 200ms ease',
        filter: hovered
          ? `drop-shadow(0 0 6px ${color}) brightness(1.2)`
          : `drop-shadow(0 0 2px ${color}40)`,
      }}
    />
  )
}

/** Clean horizontal-only grid for a calmer look (element factory). */
export function softGrid(): ReactElement {
  return <CartesianGrid strokeDasharray="3 3" stroke={GRID_COLOR} vertical={false} />
}

/** Entrance animation class names (keyframes live in index.css). */
export const RISE = {
  1: 'chart-rise d1',
  2: 'chart-rise d2',
  3: 'chart-rise d3',
} as const
