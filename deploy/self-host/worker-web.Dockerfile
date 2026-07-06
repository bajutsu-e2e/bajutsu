# Slim Linux web-worker image for the web (Playwright) backend (BE-0173). Unlike the Mac idb worker
# — which stays bare metal because it needs the Aqua GUI session for the iOS Simulator — the web
# backend runs headless on Linux, so the web worker is a container. It carries only the worker's true
# runtime closure (`bajutsu[worker-web]` = web + visual + schema) and deliberately omits the control
# plane (server/db/oauth), the cloud SDKs (worker is credential-free since BE-0160), and the AI SDK.
# Build context is the repo root (see docker-compose.yml: context ../..).

# --- Build stage: install the worker closure into a self-contained venv (non-editable). ---
FROM python:3.13-slim AS build
WORKDIR /src
COPY . /src
# A plain venv install (not `-e`) so the final stage copies a wheel-installed tree, not a checkout.
# Only bajutsu[worker-web]; the control-plane / cloud / AI wheels never enter the image.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir '.[worker-web]'

# --- Final stage: slim base + the venv + the headless Chromium shell, and nothing else. ---
FROM python:3.13-slim AS final
# A fixed browser path both root (install) and the worker user (runtime) share; PATH puts the venv's
# console scripts (`bajutsu`, `playwright`) first.
ENV PATH="/opt/venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY --from=build /opt/venv /opt/venv

# Install ONLY the Chromium headless shell, not the full headed Chromium (BE-0173): `--only-shell`
# skips the ~60-70 MB full build and pulls a lighter apt set. Playwright auto-selects the shell for
# headless launches, so the driver (bajutsu/drivers/playwright.py) needs no change — a Linux worker is
# always headless. The explicit `chromium` matters: without it, `install` would also fetch full
# firefox + webkit (the shell substitution only applies to chromium), re-inflating the image. This
# image therefore serves Chromium web runs (the default engine), not firefox/webkit (BE-0076).
# `--with-deps` installs the reduced system-library set and needs root (apt).
RUN playwright install --with-deps --only-shell chromium \
 && rm -rf /var/lib/apt/lists/*

# Chromium refuses to run as root without --no-sandbox, and the driver launches with the default
# sandboxed flags (unchanged across targets, per the app-agnostic principle). So run the worker as an
# unprivileged user instead of weakening the sandbox; it owns the shared browser path and its HOME
# (the run tree's working directory).
RUN useradd --create-home --uid 1000 worker \
 && chown -R worker:worker /ms-playwright
USER worker
WORKDIR /home/worker

# Configured entirely by environment, exactly like the bare-metal worker (BE-0106) and — per BE-0160
# — with NO object-store credentials: BAJUTSU_SERVER_URL points at the control plane, BAJUTSU_TOKEN
# authenticates. Left empty here so compose / `docker run -e` supply them.
ENV BAJUTSU_SERVER_URL="" \
    BAJUTSU_TOKEN=""
ENTRYPOINT ["bajutsu", "worker"]
