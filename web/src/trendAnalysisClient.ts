import type { TrendTradingSystemSettings } from './tradingSystems'

export async function recalculateTrendAnalysis(
  symbol: string,
  settings: TrendTradingSystemSettings,
  settingsRevision: number,
): Promise<unknown> {
  const timeframes = [
    settings.dailyEnabled && 'daily',
    settings.weeklyEnabled && 'weekly',
    settings.monthlyEnabled && 'monthly',
  ].filter((item): item is string => Boolean(item))
  const configVersion = [
    `workspace-r${settingsRevision}`,
    settings.shortHorizonBars,
    settings.mediumHorizonBars,
    settings.longHorizonBars,
    Number(settings.provisionalPreview),
    Number(settings.showTentativePivots),
  ].join('-')
  const response = await fetch('/api/analysis/trend/recalculate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      symbol,
      timeframes,
      short_horizon_bars: settings.shortHorizonBars,
      medium_horizon_bars: settings.mediumHorizonBars,
      long_horizon_bars: settings.longHorizonBars,
      config_version: configVersion,
      include_preview: settings.provisionalPreview,
    }),
  })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const payload = await response.json() as { detail?: string }
      if (payload.detail) detail = payload.detail
    } catch {
      // Keep the HTTP status when the backend did not return JSON.
    }
    throw new Error(detail)
  }
  return response.json()
}
