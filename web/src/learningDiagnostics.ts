import { logWarning } from './eventLogger'

const reported = new Set<string>()

export function reportLearningWarning(
  key: string, message: string, context?: Record<string, unknown>,
) {
  if (reported.has(key)) return
  if (reported.size >= 20) reported.clear()
  reported.add(key)
  logWarning('learning', message, context)
}
