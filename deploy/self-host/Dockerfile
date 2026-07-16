# Control-plane image for self-hosting bajutsu's single-tenant server backend (BE-0016 Tier B).
# Build context is the repo root (see docker-compose.yml: context ../..). The macOS worker is NOT
# built from this image — it runs natively on a Mac (it needs the Simulator) via `bajutsu worker`.
FROM python:3.13-slim

# The commit this image was built at (BE-0277). Passed by the build pipeline (see docker-compose.yml
# and docs/self-hosting.md); embedded as BAJUTSU_BUILD_COMMIT so serve's version badge can report it
# when there's no .git in the image to read. Left unset it stays empty and the badge's Git detection
# simply finds nothing to fall back to — an unset arg changes nothing.
ARG GIT_COMMIT=""
ENV BAJUTSU_BUILD_COMMIT=$GIT_COMMIT

WORKDIR /app
COPY . /app
# The control plane serves the FastAPI app and enqueues runs to Redis (RQ), persists to Postgres,
# and stores artifacts in S3/MinIO, with GitHub OAuth — so it needs the server, worker, db, and
# oauth extras. psycopg/sqlalchemy/boto3/fastapi/uvicorn all ship wheels, so slim needs no toolchain.
RUN pip install --no-cache-dir '.[server,worker,db,oauth]'

EXPOSE 8765
# A sensible default for `docker run`, but compose supplies the real command for both services it
# builds from this image: `alembic … upgrade head` for the one-shot `migrate`, and the serve command
# with `--config /config/bajutsu.config.yaml` for the control plane (see docker-compose.yml).
CMD ["bajutsu", "serve", "--asgi", "--backend=server", "--host", "0.0.0.0", "--port", "8765"]
