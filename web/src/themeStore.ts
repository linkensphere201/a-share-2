export type ThemeColors = {
  base: string
  surface: string
  surfaceAlt: string
  raised: string
  hover: string
  border: string
  borderStrong: string
  text: string
  textStrong: string
  muted: string
  accent: string
  accentSoft: string
  chartBackground: string
  chartGrid: string
  crosshair: string
}

export type ThemeDefinition = {
  id: string
  name: string
  mode: 'dark' | 'light'
  colors: ThemeColors
}

const dark = (
  id: string,
  name: string,
  base: string,
  surface: string,
  raised: string,
  border: string,
  text: string,
  muted: string,
  accent: string,
): ThemeDefinition => ({
  id, name, mode: 'dark', colors: {
    base, surface, surfaceAlt: raised, raised,
    hover: mix(raised, text, .1), border, borderStrong: mix(border, text, .22),
    text, textStrong: mix(text, '#ffffff', .45), muted, accent,
    accentSoft: mix(raised, accent, .3), chartBackground: base,
    chartGrid: mix(base, text, .09), crosshair: mix(muted, text, .35),
  },
})

const light = (
  id: string,
  name: string,
  base: string,
  surface: string,
  raised: string,
  border: string,
  text: string,
  muted: string,
  accent: string,
): ThemeDefinition => ({
  id, name, mode: 'light', colors: {
    base, surface, surfaceAlt: raised, raised,
    hover: mix(raised, text, .07), border, borderStrong: mix(border, text, .2),
    text, textStrong: mix(text, '#000000', .35), muted, accent,
    accentSoft: mix(raised, accent, .18), chartBackground: base,
    chartGrid: mix(base, text, .1), crosshair: mix(muted, text, .3),
  },
})

export const themes: ThemeDefinition[] = [
  dark('darkblue', 'Dark Blue', '#08111f', '#0d1929', '#14253a', '#293d55', '#c8d7e8', '#7f94aa', '#5aa7e8'),
  dark('desert', 'Desert', '#171410', '#211d17', '#2c261e', '#4a4032', '#ded1b7', '#9e9078', '#d7a95d'),
  dark('elflord', 'Elflord', '#101516', '#151d1c', '#1d2926', '#30443e', '#c8ddd5', '#78978c', '#66c2a3'),
  dark('evening', 'Evening', '#17171d', '#1d1d25', '#282833', '#3d3d4c', '#d2d1dc', '#8c8a9c', '#8b91d6'),
  dark('habamax', 'Habamax', '#111415', '#171b1c', '#202627', '#343c3e', '#d0d5d4', '#82908d', '#5fb7a2'),
  dark('industry', 'Industry', '#111416', '#181d20', '#22292d', '#384248', '#d6dadc', '#879298', '#ed9b4f'),
  dark('koehler', 'Koehler', '#080a0d', '#10141a', '#181f28', '#303a46', '#d4dae3', '#798697', '#69a8e6'),
  light('lunaperche', 'Lunaperche', '#f4f6f7', '#ffffff', '#e9eef1', '#c8d1d7', '#26343c', '#687982', '#367fa6'),
  light('morning', 'Morning', '#f7f7f3', '#ffffff', '#ecece6', '#d0d0c8', '#30312d', '#74756d', '#527f9f'),
  dark('murphy', 'Murphy', '#10160f', '#171f15', '#202b1d', '#34462f', '#d0ddca', '#829479', '#7fbd68'),
  dark('pablo', 'Pablo', '#111217', '#181a21', '#222530', '#373b48', '#d6d8e0', '#858997', '#d5829a'),
  light('peachpuff', 'Peach Puff', '#fff3e8', '#fffaf5', '#f4dfd0', '#dbbfae', '#45362f', '#806b60', '#b86646'),
  light('quiet', 'Quiet', '#f1f3f2', '#fafbfa', '#e4e8e6', '#c6ceca', '#2f3935', '#6c7a74', '#4f8173'),
  dark('retrobox', 'Retrobox', '#1d2021', '#282828', '#32302f', '#504945', '#d5c4a1', '#928374', '#d79921'),
  dark('ron', 'Ron', '#10131a', '#171c26', '#202838', '#344055', '#d0d8e8', '#8290a8', '#6d91d8'),
  light('shine', 'Shine', '#fffdf3', '#ffffff', '#f1ecd6', '#d8cfaa', '#393629', '#77705a', '#9b7b25'),
  dark('slate', 'Slate', '#11171b', '#182126', '#222d34', '#374650', '#d1dce1', '#80929b', '#5ba2b5'),
  dark('sorbet', 'Sorbet', '#171219', '#211923', '#2d2230', '#47364b', '#e0d1df', '#9c849b', '#d47aaf'),
  dark('torte', 'Torte', '#101010', '#181818', '#232323', '#3a3a3a', '#d6d6d6', '#858585', '#c4925d'),
  dark('zaibatsu', 'Zaibatsu', '#0d1217', '#121a21', '#19252e', '#2d414f', '#cbd9e2', '#788e9c', '#3fa6c9'),
]

export const themeStorageKey = 'stock-harness.theme.v1'
export const defaultThemeId = 'koehler'

export function getTheme(id: string | null | undefined): ThemeDefinition {
  return themes.find(theme => theme.id === id) ?? themes.find(theme => theme.id === defaultThemeId)!
}

export function loadTheme(): ThemeDefinition {
  try {
    return getTheme(window.localStorage.getItem(themeStorageKey))
  } catch {
    return getTheme(defaultThemeId)
  }
}

export function applyTheme(theme: ThemeDefinition): void {
  const root = document.documentElement
  root.dataset.theme = theme.id
  root.dataset.themeMode = theme.mode
  const variables: Record<string, string> = {
    '--theme-base': theme.colors.base,
    '--theme-surface': theme.colors.surface,
    '--theme-surface-alt': theme.colors.surfaceAlt,
    '--theme-raised': theme.colors.raised,
    '--theme-hover': theme.colors.hover,
    '--theme-border': theme.colors.border,
    '--theme-border-strong': theme.colors.borderStrong,
    '--theme-text': theme.colors.text,
    '--theme-text-strong': theme.colors.textStrong,
    '--theme-muted': theme.colors.muted,
    '--theme-accent': theme.colors.accent,
    '--theme-accent-soft': theme.colors.accentSoft,
  }
  Object.entries(variables).forEach(([name, value]) => root.style.setProperty(name, value))
  root.style.colorScheme = theme.mode
}

export function persistTheme(theme: ThemeDefinition): void {
  window.localStorage.setItem(themeStorageKey, theme.id)
  applyTheme(theme)
}

function mix(left: string, right: string, weight: number): string {
  const channel = (color: string, offset: number) => Number.parseInt(color.slice(offset, offset + 2), 16)
  const value = [1, 3, 5].map(offset => Math.round(channel(left, offset) * (1 - weight) + channel(right, offset) * weight))
  return `#${value.map(item => item.toString(16).padStart(2, '0')).join('')}`
}
