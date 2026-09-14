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
  onAccent: string
  secondary: string
  secondarySoft: string
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
  secondary = '#d6b774',
): ThemeDefinition => ({
  id, name, mode: 'dark', colors: {
    base, surface, surfaceAlt: mix(surface, raised, .55), raised,
    hover: mix(raised, text, .1), border, borderStrong: mix(border, text, .22),
    text, textStrong: mix(text, '#ffffff', .45), muted, accent,
    onAccent: contrastInk(accent), secondary, secondarySoft: mix(raised, secondary, .14),
    accentSoft: mix(raised, accent, .2), chartBackground: base,
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
  secondary = '#92546f',
): ThemeDefinition => ({
  id, name, mode: 'light', colors: {
    base, surface, surfaceAlt: mix(surface, raised, .55), raised,
    hover: mix(raised, text, .07), border, borderStrong: mix(border, text, .2),
    text, textStrong: mix(text, '#000000', .35), muted, accent,
    onAccent: contrastInk(accent), secondary, secondarySoft: mix(raised, secondary, .09),
    accentSoft: mix(raised, accent, .12), chartBackground: base,
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
  dark('koehler', 'Koehler', '#090c0e', '#111619', '#1a2226', '#2c393e', '#d6dfe1', '#7f9195', '#55b7a6'),
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
  light('porcelain', 'Porcelain', '#f2f5f6', '#ffffff', '#e5ecf0', '#c5cfd6', '#283842', '#5c707c', '#246c91', '#9c536b'),
  light('mint', 'Mint', '#f0f6f3', '#ffffff', '#e0eee7', '#bed4ca', '#263d34', '#576f63', '#226e56', '#91527a'),
  light('rose', 'Rose', '#faf3f5', '#ffffff', '#f1e4e9', '#d9c6cf', '#402e38', '#78606c', '#9b3d62', '#267c83'),
  light('arctic', 'Arctic', '#f2f6fa', '#ffffff', '#e3ecf6', '#c0cfe0', '#29394b', '#586e84', '#325f9e', '#987023'),
  dark('graphite', 'Graphite', '#141618', '#1c2023', '#2a3034', '#404b52', '#e1e6e8', '#9aa9b2', '#6cc9ce', '#e3b879'),
  dark('forest', 'Forest', '#101916', '#18241f', '#25352e', '#40574b', '#dce8df', '#a1b6a8', '#8fd2a8', '#e7b8ca'),
  dark('ink', 'Ink', '#17171b', '#222228', '#30303a', '#484855', '#e8e6ed', '#aaa5ba', '#e7b777', '#81cbd0'),
  dark('berry', 'Berry', '#1c151a', '#292027', '#392d36', '#55414e', '#eee1e9', '#b7a0b0', '#e6a2c0', '#91cdbb'),
].map(theme => {
  const colors = theme.colors
  const backgrounds = [colors.base, colors.surface, colors.raised, colors.hover, colors.accentSoft]
  // Small secondary labels need readable contrast even on selected rows.
  const ink = theme.mode === 'dark' ? '#ffffff' : '#000000'
  for (let step = 0; step < 30 && backgrounds.some(background => contrastRatio(colors.muted, background) < 4.5); step++) {
    colors.muted = mix(colors.muted, ink, .08)
  }
  return theme
})

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
    '--theme-on-accent': theme.colors.onAccent,
    '--theme-secondary': theme.colors.secondary,
    '--theme-secondary-soft': theme.colors.secondarySoft,
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

export function contrastRatio(left: string, right: string): number {
  const luminance = (color: string) => {
    const channels = [1, 3, 5].map(offset => Number.parseInt(color.slice(offset, offset + 2), 16) / 255)
      .map(channel => channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4)
    return channels[0] * .2126 + channels[1] * .7152 + channels[2] * .0722
  }
  const a = luminance(left), b = luminance(right)
  return (Math.max(a, b) + .05) / (Math.min(a, b) + .05)
}

function contrastInk(background: string): string {
  return contrastRatio(background, '#ffffff') >= contrastRatio(background, '#000000') ? '#ffffff' : '#000000'
}
