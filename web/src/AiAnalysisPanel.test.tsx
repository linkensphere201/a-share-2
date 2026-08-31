// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AiAnalysisPanel } from './AiAnalysisPanel'
import { projectAiReferences, type AiAnalysisReport } from './aiAnalysisClient'
import { selectCorePatternItems } from './generatedAnalysisProjection'

afterEach(cleanup)

const report: AiAnalysisReport = {
  report_id: 'report-1', symbol: '000001.SZ', timeframe: 'daily', as_of_date: '2026-08-31',
  title: '结构分析', conclusion_markdown: '[K1] 支撑有效后观察 [L1] 突破。',
  author: 'codex', revision: 2, created_at_ms: 1,
  framework: {
    key_level_codes: ['K1'],
    structures: [
      { horizon: 'small', trend: '修复', pattern: '平台', state: '形成中', reference_codes: ['K1'] },
      { horizon: 'medium', trend: '下降', pattern: '通道', state: '未突破', reference_codes: ['L1'] },
    ],
    risk_reward: [{
      name: '支撑确认', direction: 'long', trigger: 'K1止跌', entry_price: 10,
      stop_price: 9, target_price: 12, risk_reward_ratio: 2, reference_codes: ['K1', 'L1'],
    }],
  },
  references: [
    { code: 'K1', kind: 'level', label: '支撑区', detail: '近期低点', geometry: { lower: 9.9, upper: 10.1 } },
    { code: 'L1', kind: 'line', label: '中期压力线', detail: '下降高点连线', geometry: {
      start_date: '2026-08-01', start_price: 13, end_date: '2026-08-20', end_price: 12,
      role: 'resistance', horizon: 'medium',
    } },
  ],
}

describe('AiAnalysisPanel', () => {
  it('renders the mandatory framework and highlights referenced geometry', () => {
    const onHighlight = vi.fn()
    render(<AiAnalysisPanel report={report} onHighlightItemChange={onHighlight} onClose={vi.fn()}/>)

    expect(screen.getByText('小周期 · 7–14日')).toBeTruthy()
    expect(screen.getByText('中周期 · 14–28日')).toBeTruthy()
    expect(screen.getByText('2.00R')).toBeTruthy()
    const levelCode = screen.getAllByRole('button', { name: 'K1' })[0]
    fireEvent.pointerEnter(levelCode)
    expect(onHighlight).toHaveBeenLastCalledWith('ai:report-1:K1')
    fireEvent.pointerLeave(levelCode)
    expect(onHighlight).toHaveBeenLastCalledWith(undefined)
  })

  it('projects custom levels and trend lines into generated overlay items', () => {
    const items = projectAiReferences(report)
    expect(items).toEqual(expect.arrayContaining([
      expect.objectContaining({ item_id: 'ai:report-1:K1', item_type: 'zone' }),
      expect.objectContaining({ item_id: 'ai:report-1:L1', item_type: 'line' }),
    ]))
    expect(items.find(item => item.item_id.endsWith(':L1'))?.payload.horizon).toBe('long')
    expect(selectCorePatternItems([
      { item_id: 'primary', item_type: 'pattern', payload: { horizon: 'long', primary: true } },
      { item_id: 'ai-referenced', item_type: 'pattern', payload: {
        horizon: 'long', primary: false, ai_reference_code: 'P2',
      } },
    ]).map(item => item.item_id)).toEqual(['primary', 'ai-referenced'])
  })

  it('projects a frozen generated-item snapshot under the report namespace', () => {
    const withSnapshot: AiAnalysisReport = {
      ...report,
      references: [{
        code: 'P1', kind: 'pattern', label: '平台', detail: '',
        analysis_item_id: 'old-pattern',
        snapshot: {
          item_id: 'old-pattern', item_type: 'pattern',
          payload: { horizon: 'small', display_name: '平台', primary: false },
        },
      }],
    }
    const item = projectAiReferences(withSnapshot)[0]
    expect(item.item_id).toBe('ai:report-1:P1')
    expect(item.payload.ai_reference_code).toBe('P1')
  })
})
