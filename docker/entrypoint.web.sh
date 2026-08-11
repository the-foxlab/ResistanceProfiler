#!/bin/sh
# ── Non-root startup entrypoint ────────────────────────────────────
# The container starts as root so it can fix ownership of the bind-mounted
# /data volume, which is created by the host and is *not* affected by the
# `chown` baked into the image at build time (a bind mount replaces the
# image's /data directory entirely). After fixing ownership, we drop
# privileges to the unprivileged `respro` user via gosu before exec'ing the
# real command, so the application itself never runs as root.
set -e

if [ -d /data ]; then
    # chown each top-level entry under /data individually so that a read-only
    # bind mount stacked on top of a subdirectory (e.g. the worker's read-only
    # project_databases mount) is skipped instead of aborting the whole
    # startup. A read-only mount returns EROFS for chown; `chown 2>/dev/null
    # || true` swallows that single failure and the recursion continues with
    # the writable entries. The host is expected to have already set correct
    # ownership (UID/GID 1001) on any read-only-mounted directory.
    for entry in /data/*; do
        [ -e "$entry" ] || continue
        chown -R respro:respro "$entry" 2>/dev/null || true
    done
    chown respro:respro /data 2>/dev/null || true
fi

exec gosu respro "$@"
