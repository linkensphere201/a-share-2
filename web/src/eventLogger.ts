export type FrontendLogLevel = 'INFO' | 'WARNING' | 'ERROR'

let globalHandlersInstalled = false

export function installGlobalEventLogging() {
  if (globalHandlersInstalled) return
  globalHandlersInstalled = true
  window.addEventListener('error', event => {
    logError('window', 'Unhandled frontend error', {
      message: event.message,
      source: event.filename,
      line: event.lineno,
    })
  })
  window.addEventListener('unhandledrejection', event => {
    logError('window', 'Unhandled promise rejection', { reason: stringify(event.reason) })
  })
}

export function logInfo(logger: string, message: string, context?: unknown) {
  console.info(`[${logger}] ${message}`, context ?? '')
}

export function logWarning(logger: string, message: string, context?: unknown) {
  console.warn(`[${logger}] ${message}`, context ?? '')
  report('WARNING', logger, message, context)
}

export function logError(logger: string, message: string, context?: unknown) {
  console.error(`[${logger}] ${message}`, context ?? '')
  report('ERROR', logger, message, context)
}

function report(level: Exclude<FrontendLogLevel, 'INFO'>, logger: string, message: string, context?: unknown) {
  const detail = context === undefined ? message : `${message} | ${stringify(context)}`
  void fetch('/api/runtime-events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ level, logger, message: detail.slice(0, 1000) }),
  }).catch(error => console.warn('[eventLogger] Failed to report frontend event', error))
}

function stringify(value: unknown): string {
  if (value instanceof Error) return `${value.name}: ${value.message}`
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}
