#!/bin/sh
# ── Non-root startup entrypoint ────────────────────────────────────
# The container starts as root so it can fix ownership of the bind-mounted
# /data volume without dereferencing symlinks. A shared flock on /data
# serializes concurrent web/worker startups so workers never boot against
# un-owned directories or race on transient files, then privileges drop to
# the unprivileged `respro` user (UID 1001) via gosu before exec'ing the
# real command.
set -e

fix_data_ownership() {
    # Traverse physically (-P) without following symlinks and update only entries
    # whose UID/GID differs from `respro` via non-dereferencing `chown -h`.
    # Read-only submounts (e.g. :ro /data/imprint.html) return EROFS and are
    # skipped cleanly (`2>/dev/null || true`); ensure :ro host files are 0644
    # or owned by 1001:1001 so `respro` can read them.
    find -P /data \( ! -user respro -o ! -group respro \) -exec chown -h respro:respro {} + 2>/dev/null || true
}

if [ -d /data ] && [ ! -L /data ]; then
    lock_file="/data/.respro-entrypoint.lock"
    if [ ! -L "$lock_file" ] && { (set -C; : > "$lock_file") 2>/dev/null || { [ -f "$lock_file" ] && [ ! -L "$lock_file" ]; }; }; then
        exec 9<"$lock_file"
        flock -x 9
        fix_data_ownership
        flock -u 9
        exec 9<&-
    else
        # Fallback when /data itself is read-only or lockfile is unusable.
        fix_data_ownership
    fi
fi

exec gosu respro "$@"
