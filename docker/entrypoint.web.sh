#!/bin/sh
# ── Non-root startup entrypoint (SEC-005) ────────────────────────────────────
# The container starts as root so it can fix ownership of the bind-mounted
# /data volume, which is created by the host and is *not* affected by the
# `chown` baked into the image at build time (a bind mount replaces the
# image's /data directory entirely). After fixing ownership, we drop
# privileges to the unprivileged `respro` user via gosu before exec'ing the
# real command, so the application itself never runs as root.
set -e

if [ -d /data ]; then
    chown -R respro:respro /data
fi

exec gosu respro "$@"
