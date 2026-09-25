# Deploying betterweb

One small DigitalOcean droplet runs everything (PLAN.md §10), with Docker Compose
(`deploy/compose.yml`):

- `db`: Postgres 16 with pgvector.
- `api`: FastAPI.
- `web`: Caddy, which serves the built frontend, proxies `/api` and gets HTTPS certificates
  from Let's Encrypt.
- `worker`: the crawl cycle, run each night by a systemd timer.

Each process connects to Postgres as its own role (PLAN.md §4):
- The API connects as `discovery_api_login`.
- The crawl stages connect as `discovery_crawl_login`, which can't see user data.
- The scoring stage connects as `discovery_score_login`.

Around them:
- A nightly backup goes to a Spaces bucket.
- Alert emails go out when a crawl or backup fails, or the disk passes 80%.

Expected cost: about $24/month for the droplet, $5 for Spaces and a few dollars of
OpenRouter. Resend's free tier covers the alerts.

## 1. Accounts and resources (once)

1. **Droplet.** Create an Ubuntu 24.04 droplet in NYC3:
   - Size: Basic, Regular, 2 vCPU / 4 GB / 80 GB ($24/month).
   - Log in with an SSH key, not a password.
   - Add a **Cloud Firewall** allowing inbound TCP 22 (ideally only from your IP), TCP 80,
     TCP 443 and UDP 443.
2. **DNS.** Point the site's name at the droplet with an `A` record, e.g. `betterweb` in
   `brians.cafe` → the droplet's IPv4 address. Caddy can't get a certificate until this
   resolves.
3. **Spaces.** Create a bucket (e.g. `betterweb-backups`) in NYC3 with file listing
   restricted. Under *API → Spaces Keys*, create a key limited to that bucket with read/write
   access.
4. **Resend** (alert emails; DigitalOcean blocks outgoing SMTP from droplets):
   - Sign up at resend.com with the address that should receive alerts.
   - Create an API key with *Sending access*.
   - Alerts are sent from Resend's shared `onboarding@resend.dev`, which may deliver only to
     your account's own address. To alert anyone else, verify a domain in Resend and set
     `ALERT_EMAIL_FROM`.
5. **OpenRouter.** Use a key with a monthly credit limit set in OpenRouter. The app also
   stops at `PROVIDER_MONTHLY_SPEND_CAP_USD`.

## 2. Install

On the droplet, as root:

```sh
curl -fsSL https://get.docker.com | sh
git clone https://github.com/brliang/betterweb.git /opt/betterweb
cd /opt/betterweb/deploy
cp .env.example .env
nano .env   # fill in every value; `openssl rand -hex 24` makes each password
bin/install
```

`bin/install` does the following:
1. Builds the images and starts `db`, `api` and `web`. Migrations and the database login
   users run first.
2. Loads and embeds the topic taxonomy, which costs a few cents the first time.
3. Installs these systemd timers:
   - `betterweb-cycle`: the crawl, at `CYCLE_LOCAL_START` in `CYCLE_TIMEZONE`.
   - `betterweb-backup`: daily at noon.
   - `betterweb-disk`: daily at 9am.

Their `OnFailure=` handler, `betterweb-alert@`, emails the failed run's log lines.

Then set the backups to expire after 14 days (once per bucket):

```sh
docker compose run --rm --no-deps -T --entrypoint sh backup -c \
  'aws s3api put-bucket-lifecycle-configuration --bucket "$SPACES_BUCKET" \
   --lifecycle-configuration "$0"' "$(cat spaces-lifecycle.json)"
```

Check that the pieces work:

```sh
curl -s https://betterweb.brians.cafe/api/health         # {"status":"ok"}
echo "test alert" | docker compose run --rm --no-deps -T worker alert send "test"
systemctl start betterweb-backup.service && journalctl -u betterweb-backup -n 5
```

## 3. Sign in (and bring your preferences)

To carry pins, interests and settings over from a local stack, export them there:

```sh
docker compose run --rm --no-deps -T worker users export you@example.com > me.json
scp me.json root@<droplet>:/opt/betterweb/deploy/
```

Then import them on the droplet. This pins your sites, which seeds the first crawl.

```sh
docker compose exec -T api python -m app.worker users import < me.json && rm me.json
```

Likes, hides and history stay behind, because they point at the old crawl's documents.
Without an export, sign in and take the survey instead.

To sign in, print a one-time link. It is valid for 30 minutes and can be used once.

```sh
docker compose exec api python -m app.worker users login-link you@example.com
```

User commands run in the `api` container, because only its role may write user data.

## 4. Running it

- **The crawl.** It starts nightly on its own. To start one now:
  `systemctl start --no-block betterweb-cycle`.
  - Follow it with `journalctl -fu betterweb-cycle`.
  - An interrupted crawl resumes on the next run.
  - Re-run single stages with
    `docker compose run --rm -T worker cycle run --stage scores` (also `extract`, `embed`).
- **Updating.** `cd /opt/betterweb && git pull && deploy/bin/install`. It is safe to repeat,
  and it applies new migrations.
- **Logs.** `docker compose logs -f api web`. The crawl logs go to `journalctl -u betterweb-cycle`.
- **Timers.** `systemctl list-timers 'betterweb-*'`.
- **Rotating a database password.** Change it in `.env`, then run
  `docker compose up -d --force-recreate`. `migrate` sets the new password before `api` starts.

## 5. Restoring a backup

Each backup is a `pg_dump` of the whole database, except fetched pages that were still waiting
for extraction; the next crawl fetches those again. To restore onto this droplet or a new one
(after section 2):

```sh
cd /opt/betterweb/deploy
docker compose run --rm --no-deps -T --entrypoint aws backup s3 ls "s3://<bucket>/db/"
docker compose run --rm --no-deps -T --entrypoint aws backup \
  s3 cp "s3://<bucket>/db/<file>.dump" /backups/restore.dump
docker compose stop api
docker compose exec -T db pg_restore -U discovery -d discovery --clean --if-exists \
  < /var/tmp/betterweb-backups/restore.dump
docker compose up -d && rm /var/tmp/betterweb-backups/restore.dump
```

The login users aren't part of the dump: they belong to the Postgres server, and `migrate`
creates them.
