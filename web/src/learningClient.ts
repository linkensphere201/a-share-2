export interface LearningSystem {
  system_id: string
  title: string
  methodology: string
  status: string
  default: boolean
  available: boolean
}

export async function openDefaultLearningSystem(): Promise<LearningSystem> {
  const catalogResponse = await fetch('/api/learning/systems')
  if (!catalogResponse.ok) throw new Error(`课程目录读取失败：HTTP ${catalogResponse.status}`)
  const catalog = await catalogResponse.json() as { items?: LearningSystem[] }
  const available = (catalog.items ?? []).filter(item => item.available)
  const system = available.find(item => item.default) ?? available[0]
  if (!system) throw new Error('尚未发布可用的交易系统教程')

  const openResponse = await fetch(
    `/api/learning/systems/${encodeURIComponent(system.system_id)}/open`,
    { method: 'POST' },
  )
  if (!openResponse.ok) throw new Error(`教程打开失败：HTTP ${openResponse.status}`)
  return system
}
