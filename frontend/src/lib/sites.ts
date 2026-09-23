/** A rough check before the server's: a host with a dot, with or without a scheme and path. */
export function looksLikeSite(text: string): boolean {
  return /^(https?:\/\/)?[^\s/.]+(\.[^\s/.]+)+(\/\S*)?$/i.test(text.trim())
}
