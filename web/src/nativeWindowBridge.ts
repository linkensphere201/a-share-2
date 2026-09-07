import type { NativeWindowGeometry } from './workspace'

export type PopoutTarget = {
  groupId: string
  windowId: string
}

export type NativeWindowResult = {
  ok: boolean
  state: string
  limit?: number
}

export type NativeWindowDiagnostic = Record<string, string | number | boolean | null | undefined>

type DesktopWindowApi = {
  pop_out_window: (
    groupId: string,
    windowId: string,
    title: string,
    geometry?: NativeWindowGeometry,
    diagnostic?: NativeWindowDiagnostic,
  ) => Promise<NativeWindowResult>
  dock_window: (groupId: string, windowId: string) => Promise<NativeWindowResult>
  focus_window: (groupId: string, windowId: string) => Promise<NativeWindowResult>
  report_window_diagnostic: (
    eventName: string,
    diagnostic?: NativeWindowDiagnostic,
  ) => Promise<NativeWindowResult>
}

declare global {
  interface Window {
    pywebview?: { api?: Partial<DesktopWindowApi> }
  }
}

export function readPopoutTarget(location: Pick<Location, 'search'> = window.location): PopoutTarget | undefined {
  const params = new URLSearchParams(location.search)
  const groupId = params.get('popoutGroupId')
  const windowId = params.get('popoutWindowId')
  return groupId && windowId ? { groupId, windowId } : undefined
}

export async function popOutNativeWindow(
  target: PopoutTarget,
  title: string,
  geometry?: NativeWindowGeometry,
  diagnostic?: NativeWindowDiagnostic,
): Promise<NativeWindowResult> {
  const api = await resolveApi('pop_out_window')
  if (!api?.pop_out_window) return { ok: false, state: 'native-shell-unavailable' }
  return api.pop_out_window(target.groupId, target.windowId, title, geometry, diagnostic)
}

export async function dockNativeWindow(target: PopoutTarget): Promise<NativeWindowResult> {
  const api = await resolveApi('dock_window')
  if (!api?.dock_window) return { ok: false, state: 'native-shell-unavailable' }
  return api.dock_window(target.groupId, target.windowId)
}

export async function focusNativeWindow(target: PopoutTarget): Promise<NativeWindowResult> {
  const api = await resolveApi('focus_window')
  if (!api?.focus_window) return { ok: false, state: 'native-shell-unavailable' }
  return api.focus_window(target.groupId, target.windowId)
}

export async function reportNativeWindowDiagnostic(
  eventName: string,
  diagnostic?: NativeWindowDiagnostic,
): Promise<NativeWindowResult> {
  const api = await resolveApi('report_window_diagnostic')
  if (!api?.report_window_diagnostic) return { ok: false, state: 'native-shell-unavailable' }
  return api.report_window_diagnostic(eventName, diagnostic)
}

async function resolveApi(method: keyof DesktopWindowApi): Promise<Partial<DesktopWindowApi> | undefined> {
  if (window.pywebview?.api?.[method]) return window.pywebview.api
  await new Promise<void>(resolve => {
    const timeout = window.setTimeout(resolve, 800)
    const ready = () => {
      window.clearTimeout(timeout)
      resolve()
    }
    window.addEventListener('pywebviewready', ready, { once: true })
  })
  return window.pywebview?.api
}
