# bribot

bribot is the crawler of [betterweb](https://github.com/brliang/betterweb), a small,
open-source discovery and search engine. It identifies itself as:

```
bribot/0.1 (+https://github.com/brliang/betterweb/blob/main/docs/bot.md)
```

If you found this page in your server logs, this is what the bot is doing and how to stop it.

## What it does

- It starts from sites that the engine's users chose to follow, and fetches their pages,
  RSS/Atom feeds and sitemaps. From there it follows links at most one site away.
- It reads each page's text, title and links so that the page can be recommended to users and
  found in search, with a link back to you. It shows users a title and a short excerpt, never
  your full text. Parts of the text are sent to AI models (through OpenRouter) to work out what
  the page is about, and, when a user asks, to write a sentence on why they might like it.
- It normally runs once a night, and doesn't refetch a page within 20 hours of the last
  fetch.

## How it behaves

- It obeys `robots.txt`, rereading it at least once a day. If a site's robots.txt disallows
  it, it fetches nothing else from that site. If robots.txt can't be read (a server error or
  timeout), it follows the last copy it read, or fetches nothing when it has none.
- It fetches one page at a time from each site, and paces itself by how quickly your site
  answers: after each response it waits about twice as long as that response took, never less
  than half a second or more than ten. It starts new sites at one second, and goes back to at
  least one second after any error. A `Crawl-delay` in your robots.txt is always respected, and
  is the way to slow it down further. Sites under one domain count as one: every blog on
  `*.bearblog.dev` shares a single pace.
- It slows down when it gets `429` or `5xx` responses, doubling its wait each time, and stops
  visiting a site for the rest of the day after five errors in a row.
- It treats `401` and `403` answers as a refusal: they never speed it up, and after three in a
  row it stops visiting the site for the rest of the day.
- It honors `noindex` and `nofollow` in robots meta tags and `X-Robots-Tag` headers, and does
  not follow links marked `rel="nofollow"`, `"ugc"` or `"sponsored"`.
- It never logs in, fills in forms or runs JavaScript, and skips login, sign-up and checkout
  pages. It never uses proxies or other addresses to get around a block.

## How to block it

Add this to your site's `robots.txt`:

```
User-agent: bribot
Disallow: /
```

It takes effect the next time the bot reads your robots.txt, within a day.

## Contact

To report a problem or ask for anything else, open an issue at
<https://github.com/brliang/betterweb/issues>.
