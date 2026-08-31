import type { GeneratedAnalysisItem } from './trendAnalysisClient'

export type AiAnalysisReference = {
  code: string
  kind: 'level' | 'line' | 'pattern'
  label: string
  detail: string
  analysis_item_id?: string | null
  geometry?: {
    lower?: number
    upper?: number
    start_date?: string
    start_price?: number
    end_date?: string
    end_price?: number
    role?: 'support' | 'resistance'
    horizon?: 'small' | 'medium'
  } | null
  snapshot?: GeneratedAnalysisItem
}

export type AiStructureView = {
  horizon: 'small' | 'medium'
  trend: string
  pattern: string
  state: string
  reference_codes: string[]
}

export type AiRiskRewardScenario = {
  name: string
  direction: 'long' | 'short'
  trigger: string
  entry_price: number
  stop_price: number
  target_price: number
  risk_reward_ratio: number
  reference_codes: string[]
}

export type AiAnalysisReport = {
  report_id: string
  symbol: string
  timeframe: string
  as_of_date: string
  source_run_id?: string | null
  title: string
  conclusion_markdown: string
  framework: {
    key_level_codes: string[]
    structures: AiStructureView[]
    risk_reward: AiRiskRewardScenario[]
  }
  references: AiAnalysisReference[]
  author: string
  revision: number
  created_at_ms: number
}

export async function loadLatestAiAnalysis(
  symbol: string,
  timeframe = 'daily',
  signal?: AbortSignal,
): Promise<AiAnalysisReport | null> {
  const response = await fetch(
    `/api/analysis/ai/${encodeURIComponent(symbol)}?timeframe=${encodeURIComponent(timeframe)}`,
    { signal },
  )
  if (response.status === 404) return null
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const value = await response.json() as Partial<AiAnalysisReport>
  if (typeof value.report_id !== 'string' || !Array.isArray(value.references)
    || !value.framework || !Array.isArray(value.framework.structures)
    || !Array.isArray(value.framework.risk_reward)) {
    throw new Error('AI analysis response is malformed')
  }
  return value as AiAnalysisReport
}

export function aiReferenceItemId(report: AiAnalysisReport, reference: AiAnalysisReference): string {
  return reference.snapshot || !reference.analysis_item_id
    ? `ai:${report.report_id}:${reference.code}`
    : reference.analysis_item_id
}

export function projectAiReferences(report: AiAnalysisReport | null): GeneratedAnalysisItem[] {
  if (!report || !Array.isArray(report.references)) return []
  return report.references.flatMap<GeneratedAnalysisItem>(reference => {
    const itemId = aiReferenceItemId(report, reference)
    if (reference.snapshot) return [{
      ...reference.snapshot,
      item_id: itemId,
      payload: {
        ...reference.snapshot.payload,
        ai_reference_code: reference.code,
      },
    }]
    if (reference.analysis_item_id || !reference.geometry) return []
    if (reference.kind === 'level'
      && typeof reference.geometry.lower === 'number'
      && typeof reference.geometry.upper === 'number') {
      return [{
        item_id: itemId,
        item_type: 'zone' as const,
        payload: {
          kind: 'key-level',
          lower: reference.geometry.lower,
          upper: reference.geometry.upper,
          center: (reference.geometry.lower + reference.geometry.upper) / 2,
          score: 2,
          ai_reference_code: reference.code,
        },
      }]
    }
    if (reference.kind === 'line'
      && reference.geometry.role
      && reference.geometry.start_date
      && reference.geometry.end_date
      && typeof reference.geometry.start_price === 'number'
      && typeof reference.geometry.end_price === 'number') {
      return [{
        item_id: itemId,
        item_type: 'line' as const,
        payload: {
          kind: reference.geometry.role,
          horizon: reference.geometry.horizon === 'medium' ? 'long' : 'short',
          first_pivot_date: reference.geometry.start_date,
          first_price: reference.geometry.start_price,
          second_pivot_date: reference.geometry.end_date,
          second_price: reference.geometry.end_price,
          score: 2,
          touch_count: 0,
          ai_reference_code: reference.code,
        },
      }]
    }
    return []
  })
}
