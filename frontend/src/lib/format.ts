const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' })
const dateTimeFormat = new Intl.DateTimeFormat(undefined, {
  dateStyle: 'medium',
  timeStyle: 'short',
})
const percentFormat = new Intl.NumberFormat(undefined, {
  style: 'percent',
  maximumFractionDigits: 1,
})
const usdFormat = new Intl.NumberFormat(undefined, {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 2,
})
const numberFormat = new Intl.NumberFormat()

export function formatDate(iso: string): string {
  return dateFormat.format(new Date(iso))
}

export function formatDateTime(iso: string): string {
  return dateTimeFormat.format(new Date(iso))
}

/** A calendar day ("2026-09-23") without shifting it through a timezone. */
export function formatDay(day: string): string {
  const [year = 0, month = 1, date = 1] = day.split('-').map(Number)
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(
    new Date(year, month - 1, date),
  )
}

export function formatPercent(share: number): string {
  return percentFormat.format(share)
}

export function formatUsd(amount: number): string {
  return usdFormat.format(amount)
}

export function formatNumber(value: number): string {
  return numberFormat.format(value)
}

/** A score component's number: short, with enough digits to compare. */
export function formatScore(value: number): string {
  return value.toFixed(3)
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)} s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${String(minutes)} min ${String(Math.round(seconds % 60))} s`
  return `${String(Math.floor(minutes / 60))} h ${String(minutes % 60)} min`
}
