import { useEffect, useRef } from 'react'

/** A keyed request may publish only while its owning selection remains active. */
export function useResultQuery<T>(
  key: string | undefined,
  load: (signal: AbortSignal) => Promise<T>,
  publish: (value: T, startedAt: number) => void,
  reject: (error: unknown) => void,
) {
  const callbacks = useRef({ load, publish, reject })
  callbacks.current = { load, publish, reject }
  useEffect(() => {
    if (key === undefined) return
    const controller = new AbortController()
    const current = callbacks.current
    const startedAt = performance.now()
    Promise.resolve().then(() => current.load(controller.signal)).then(value => {
      if (!controller.signal.aborted) current.publish(value, startedAt)
    }).catch(error => { if (!controller.signal.aborted) current.reject(error) })
    return () => controller.abort()
  }, [key])
}

/** Await each poll before scheduling another; invalidate in-flight work on key change. */
export function useSerialPolling(
  key: string | undefined, delay: number,
  poll: (signal: AbortSignal) => Promise<void>, reject: (error: unknown) => void,
) {
  const callbacks = useRef({ poll, reject })
  callbacks.current = { poll, reject }
  useEffect(() => {
    if (key === undefined) return
    const controller = new AbortController()
    let timer: number
    const tick = async () => {
      try { await callbacks.current.poll(controller.signal) }
      catch (error) { if (!controller.signal.aborted) callbacks.current.reject(error) }
      if (!controller.signal.aborted) timer = window.setTimeout(tick, delay)
    }
    timer = window.setTimeout(tick, delay)
    return () => { controller.abort(); window.clearTimeout(timer) }
  }, [key, delay])
}
