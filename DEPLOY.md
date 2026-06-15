# Deploying Narrator to a VPS

Narrator is packaged as a container image published to GHCR by CI, and wired into
a server built from
[server-instance-template](https://github.com/uppertoe/server-instance-template)
as an app under `apps/narrator/`.

## 1. Publish the image (this repo)

CI (`.github/workflows/ci.yml`) runs the tests, then on every push to `main`
(and on `v*` tags) builds the image and pushes it to:

```
ghcr.io/<owner>/narrator:latest      # main
ghcr.io/<owner>/narrator:vX.Y.Z      # version tags
ghcr.io/<owner>/narrator:sha-<sha>
```

No secrets to configure — it uses the built-in `GITHUB_TOKEN`. After the first
publish, make the GHCR package accessible to the server: either set the package
to **public**, or grant the server's pull credentials read access (Settings →
Packages → narrator → Package settings).

The image:
- multi-stage Python build via `uv` (prod deps only);
- **bakes the `base.en` Whisper model** so the container needs no network at
  runtime and runs on a **read-only filesystem**;
- runs as nonroot `65532`, writes only to the `/data` volume (SQLite) and tmpfs `/tmp`;
- serves on `:8000` with a `/healthz` endpoint.

> Built for `linux/amd64`. For an arm64 VPS, add `linux/arm64` +
> `docker/setup-qemu-action` to the publish job (slower — the model bake runs
> under emulation).

## 2. Add it to your server repo

In your `server-[name]` repo (created from the template):

```bash
mkdir -p apps/narrator
cp /path/to/narrator/deploy/docker-compose.yml apps/narrator/
cp /path/to/narrator/deploy/narrator.caddy     apps/narrator/
cp /path/to/narrator/deploy/.env.example       apps/narrator/.env.example
```

Then:

1. **Pin the image** in `apps/narrator/docker-compose.yml` to a released tag by
   digest (Renovate will keep it bumped), e.g.
   `ghcr.io/<owner>/narrator:v0.1.0@sha256:…`.
2. **Enable the app** — add one line to the root `docker-compose.yml`:
   ```yaml
   include:
     - apps/narrator/docker-compose.yml
   ```
3. **Set `DOMAIN`** in the server root `.env` (the app is served at
   `narrator.<DOMAIN>`; the `.caddy` snippet is picked up automatically).
4. *(Optional)* `cp apps/narrator/.env.example apps/narrator/.env` and set
   `ANTHROPIC_API_KEY` to enable Claude extraction.
5. *(Optional, recommended for non-public demos)* put it behind the auth gateway:
   enable `apps/auth` and add `import protected` to `apps/narrator/narrator.caddy`.

Commit, push, and deploy per the template's provisioning/deploy steps. Caddy
provisions TLS for `narrator.<DOMAIN>` automatically.

## Data & persistence

State lives in the `narrator_data` volume (`/data/narrator.db`). The Whisper
model is in the image, not the volume. Back up `narrator_data` with the
template's backup mechanism if you want case data to survive a rebuild.

## Local image smoke test

```bash
docker build -t narrator:dev .
docker run --rm -p 8000:8000 narrator:dev
# → http://localhost:8000  (the /data volume is created automatically; for a
#   throwaway run SQLite writes inside the container)
```
