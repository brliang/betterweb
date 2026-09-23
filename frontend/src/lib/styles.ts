// Shared Tailwind class lists for controls that appear on every page.

const focus =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-teal-600 dark:focus-visible:outline-teal-400'

export const primaryButton = `${focus} inline-flex items-center justify-center gap-1.5 rounded-md bg-teal-700 px-3.5 py-2 text-sm font-medium text-white hover:bg-teal-800 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-teal-600 dark:hover:bg-teal-500`

export const secondaryButton = `${focus} inline-flex items-center justify-center gap-1.5 rounded-md border border-stone-300 bg-white px-3.5 py-2 text-sm font-medium text-stone-800 hover:bg-stone-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-stone-600 dark:bg-stone-800 dark:text-stone-100 dark:hover:bg-stone-700`

export const quietButton = `${focus} inline-flex items-center gap-1 rounded-md px-2 py-1 text-sm text-stone-600 hover:bg-stone-100 hover:text-stone-900 disabled:cursor-not-allowed disabled:opacity-50 aria-pressed:text-teal-700 dark:text-stone-400 dark:hover:bg-stone-800 dark:hover:text-stone-100 dark:aria-pressed:text-teal-400`

export const textInput = `${focus} block w-full rounded-md border border-stone-300 bg-white px-3 py-2 text-sm text-stone-900 placeholder:text-stone-400 dark:border-stone-600 dark:bg-stone-900 dark:text-stone-100`

export const link = `${focus} rounded-sm text-teal-700 underline-offset-2 hover:underline dark:text-teal-400`

export const card =
  'rounded-lg border border-stone-200 bg-white p-4 dark:border-stone-700 dark:bg-stone-900'

export const choiceCard = `flex cursor-pointer gap-3 rounded-lg border border-stone-200 bg-white p-3 has-checked:border-teal-600 has-checked:bg-teal-50 has-focus-visible:outline-2 has-focus-visible:outline-teal-600 dark:border-stone-700 dark:bg-stone-900 dark:has-checked:border-teal-500 dark:has-checked:bg-teal-950`
