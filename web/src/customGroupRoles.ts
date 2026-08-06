import type { Instrument } from './workspace'

export type CustomGroupRole =
  | ''
  | 'sentiment_anchor'
  | 'liquidity_anchor'
  | 'bellwether'
  | 'core_identity'
  | 'lagging_expansion'

export type CustomGroupMember = Instrument & {
  role?: CustomGroupRole
  tags: string[]
  note: string
  available?: boolean
}

export const customGroupRoleDefinitions: Array<{
  value: Exclude<CustomGroupRole, ''>
  label: string
  description: string
}> = [
  { value: 'sentiment_anchor', label: '情绪锚点', description: '观察板块情绪、主动性与分歧承接' },
  { value: 'liquidity_anchor', label: '容量锚点', description: '高流动性资金承载与板块强度参照' },
  { value: 'bellwether', label: '中军', description: '产业逻辑稳定、机构认可度较高的趋势核心' },
  { value: 'core_identity', label: '核心标识度', description: '细分业务映射清晰、市场辨识度较高' },
  { value: 'lagging_expansion', label: '扩散补涨后排', description: '主线扩散、补涨或产业对标观察' },
]

export function customGroupRoleLabel(role?: CustomGroupRole): string {
  return customGroupRoleDefinitions.find(item => item.value === role)?.label ?? '未分类'
}
