// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'

import { applyTheme, contrastRatio, defaultThemeId, getTheme, loadTheme, persistTheme, themes, themeStorageKey } from './themeStore'

afterEach(() => {
  window.localStorage.clear()
  document.documentElement.removeAttribute('data-theme')
  document.documentElement.removeAttribute('data-theme-mode')
  document.documentElement.removeAttribute('style')
})

describe('workstation themes', () => {
  it('provides twenty-eight distinct schemes and preserves the default', () => {
    expect(themes).toHaveLength(28)
    expect(new Set(themes.map(theme => theme.id)).size).toBe(28)
    expect(loadTheme().id).toBe(defaultThemeId)
  })

  it.each(themes)('$id keeps secondary labels and primary commands readable', theme => {
    const c = theme.colors
    Object.values(c).forEach(color => expect(color).toMatch(/^#[0-9a-f]{6}$/i))
    for (const background of [c.base, c.surface, c.raised, c.hover, c.accentSoft]) {
      expect(contrastRatio(c.muted, background)).toBeGreaterThanOrEqual(4.5)
      expect(contrastRatio(c.text, background)).toBeGreaterThanOrEqual(4.5)
    }
    expect(contrastRatio(c.onAccent, c.accent)).toBeGreaterThanOrEqual(4.5)
    persistTheme(theme)
    expect(loadTheme().id).toBe(theme.id)
    expect(document.documentElement.style.getPropertyValue('--theme-secondary')).toBe(c.secondary)
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
