// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'

import { applyTheme, defaultThemeId, getTheme, loadTheme, persistTheme, themes, themeStorageKey } from './themeStore'

afterEach(() => {
  window.localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  document.documentElement.removeAttribute('data-theme-mode')
  document.documentElement.removeAttribute('style')
})

describe('workstation themes', () => {
  it('provides exactly twenty distinct schemes and a stable default', () => {
    expect(themes).toHaveLength(20)
    expect(new Set(themes.map(theme => theme.id)).size).toBe(20)
    expect(loadTheme().id).toBe(defaultThemeId)
  })

  it('persists and applies the selected scheme as CSS tokens', () => {
    const selected = getTheme('peachpuff')
    persistTheme(selected)

    expect(window.localStorage.getItem(themeStorageKey)).toBe('peachpuff')
    expect(loadTheme()).toBe(selected)
    expect(document.documentElement.dataset.theme).toBe('peachpuff')
    expect(document.documentElement.dataset.themeMode).toBe('light')
    expect(document.documentElement.style.getPropertyValue('--theme-base')).toBe(selected.colors.base)

    applyTheme(getTheme('slate'))
    expect(document.documentElement.dataset.theme).toBe('slate')
  })
})
