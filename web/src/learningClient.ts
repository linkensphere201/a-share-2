export interface LearningSystem {
  system_id: string
  title: string
  methodology: string
  status: string
  default: boolean
  available: boolean
  index_path: string
  corpus_version: string
  publication_version: string
}

export async function listLearningSystems(): Promise<LearningSystem[]> {
  const response = await fetch('/api/learning/systems')
  if (!response.ok) throw new Error(`课程目录读取失败：HTTP ${response.status}`)
  const payload = await response.json() as { items?: LearningSystem[] }
  return (payload.items ?? []).filter(item => item.available)
}

export function learningSystemUrl(system: LearningSystem): string {
  return `/learning/${system.index_path}`
}

export async function openDefaultLearningSystem(): Promise<LearningSystem> {
  const available = await listLearningSystems()
  const system = available.find(item => item.default) ?? available[0]
  if (!system) throw new Error('尚未发布可用的交易系统课程')
  const response = await fetch(
    `/api/learning/systems/${encodeURIComponent(system.system_id)}/open`,
    { method: 'POST' },
  )
  if (!response.ok) throw new Error(`教程打开失败：HTTP ${response.status}`)
  return system
}
