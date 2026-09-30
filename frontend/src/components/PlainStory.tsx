import type { Explanation } from '../types'
import { plainStory, probHeadline, trendArrow, type ModelThresholds } from '../utils/plain'

/**
 * "In everyday words" — the same real data, said the way a person would say it.
 * The exact numbers remain on the page (tables/charts); this card adds meaning.
 * The headline is always the honest model-estimate wording, with the prediction
 * horizon and (separately) the data quality.
 */
export default function PlainStory({
  ex, thresholds, title = 'In everyday words',
}: {
  ex: Explanation
  thresholds: ModelThresholds | null
  title?: string
}) {
  const story = plainStory(ex, thresholds)
  const horizon = ex.prediction_horizon ?? '0–24 h (nowcast)'

  return (
    <section className="bg-panel-sky rounded-xl border border-sky-900/60 p-4">
      <h2 className="text-sm font-semibold text-sky-300 uppercase tracking-wide mb-2">{title}</h2>

      {ex.missing_critical ? (
        <p className="text-sm text-night-100">
          <b>{story.verdict}</b> Nothing is invented to fill the gap. Follow official
          IMD/NDMA channels meanwhile.
        </p>
      ) : (
        <>
          <p className="text-sm text-night-50">
            <b>{story.verdict}</b>
          </p>
          <p className="text-sm text-night-200 mt-1">
            {probHeadline(ex.probability)} for <b>{horizon}</b> — a hydrological threshold
            exceedance estimate, not a probability of an officially confirmed flood.
          </p>
        </>
      )}

      <ul className="mt-3 text-sm space-y-1 list-disc list-inside text-night-200">
        {story.conditions.map((c, i) => <li key={i}>{c}</li>)}
      </ul>

      <p className="mt-3 text-sm text-night-100">
        <b>What you can do: </b>{story.advice}
      </p>

      <div className="mt-3 pt-2 border-t border-sky-900/60 grid sm:grid-cols-3 gap-2 text-xs text-night-300">
        <div>
          <span className="text-night-400">Trend: </span>
          <b>{trendArrow(ex.trend)}</b>
        </div>
        <div>
          <span className="text-night-400">Data quality: </span>
          <b>{ex.data_quality ?? (ex.missing_critical ? 'insufficient data' : 'fair')}</b>
        </div>
        <div>
          <span className="text-night-400">Uncertainty: </span>
          not numerically quantified
        </div>
      </div>

      {!thresholds && !ex.missing_critical && (
        <p className="mt-2 text-xs text-amber-400">
          Risk classes (Low/Moderate/High) are temporarily unavailable — the raw numbers on
          this page are still real.
        </p>
      )}
    </section>
  )
}
