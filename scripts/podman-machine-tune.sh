#!/usr/bin/env bash
# Keep a podman host's journal for what it is for: incidents.
#
# Two host-wide settings that no wip-deploy install owns, so this script is an
# explicit step an operator runs (once per podman machine or Linux host; it is
# safe to run again):
#
#   1. The podman API service runs with LOGGING="--log-level=info", and every
#      container healthcheck runs as a transient unit that copies that flag:
#      four info lines per probe, about 9,000 journal lines per 10 minutes for
#      one dev stack. A drop-in sets the service to --log-level=warn. A
#      healthcheck unit is written when its container starts, so running
#      containers keep the old level until they are restarted; the script
#      lists them.
#   2. With --journal-max SIZE, a journald drop-in caps the journal at SIZE
#      (SystemMaxUse). Size it from --measure, taken once the noisy sources
#      are fixed, for the days of history you want to keep.
#
# Usage:
#   scripts/podman-machine-tune.sh [--measure] [--journal-max SIZE]
#                                  [--machine NAME] [--dry-run]
#
#   --measure          only report: journal lines per 10 minutes by source,
#                      and the journal's size. Changes nothing.
#   --journal-max SIZE also write /etc/systemd/journald.conf.d/wip.conf with
#                      SystemMaxUse=SIZE (e.g. 2G) and restart journald.
#   --machine NAME     the podman machine (macOS; default: the default one).
#   --dry-run          print what would run, change nothing.
#
# On macOS the commands run inside the podman machine (podman machine ssh);
# on Linux, on this host.

set -euo pipefail

MEASURE=0
JOURNAL_MAX=""
MACHINE=""
DRY_RUN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --measure) MEASURE=1 ;;
        --journal-max) JOURNAL_MAX="${2:?--journal-max needs a size, e.g. 2G}"; shift ;;
        --machine) MACHINE="${2:?--machine needs a name}"; shift ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) sed -n '2,32p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

case "$JOURNAL_MAX" in
    ""|[0-9]*[KMGT]) ;;
    *) echo "--journal-max takes a size such as 512M or 2G, not '$JOURNAL_MAX'" >&2; exit 2 ;;
esac

# Run a shell snippet where podman's services live: in the machine on macOS,
# here on Linux.
on_host() {
    if [ "$(uname -s)" = "Darwin" ]; then
        podman machine ssh ${MACHINE:+"$MACHINE"} -- "$1"
    else
        bash -c "$1"
    fi
}

MEASURE_SNIPPET='
echo "journal lines in the last 10 minutes: $(journalctl --since -10min -q | wc -l)"
echo "by source (top 8):"
journalctl --since -10min -q -o json --output-fields=SYSLOG_IDENTIFIER 2>/dev/null \
    | grep -o "\"SYSLOG_IDENTIFIER\":\"[^\"]*\"" | cut -d\" -f4 \
    | sort | uniq -c | sort -rn | head -8
echo "podman info lines: $(journalctl --since -10min -q -t podman | grep -c "level=info" || true)"
journalctl --disk-usage
'

LOG_LEVEL_SNIPPET='
set -e
conf="[Service]
Environment=LOGGING=\"--log-level=warn\""
dir="$HOME/.config/systemd/user/podman.service.d"
mkdir -p "$dir"
printf "%s\n" "$conf" > "$dir/wip-log-level.conf"
systemctl --user daemon-reload
systemctl --user try-restart podman.service podman.socket || true
if systemctl cat podman.service >/dev/null 2>&1; then
    sudo mkdir -p /etc/systemd/system/podman.service.d
    printf "%s\n" "$conf" | sudo tee /etc/systemd/system/podman.service.d/wip-log-level.conf >/dev/null
    sudo systemctl daemon-reload
    sudo systemctl try-restart podman.service podman.socket || true
fi
echo "podman service log level: warn (user and system units)"
left=0
for scope in --user ""; do
    for unit in $(systemctl $scope list-units --all --no-legend --plain 2>/dev/null \
            | awk "{print \$1}" | grep -E "^[0-9a-f]{64}-[0-9a-f]+\.service$" || true); do
        if systemctl $scope cat "$unit" 2>/dev/null | grep -q "\"--log-level\" \"info\""; then
            left=$((left + 1))
            echo "  still at info: container ${unit%%-*}"
        fi
    done
done
if [ "$left" -gt 0 ]; then
    echo "$left healthcheck(s) still run at info until their containers restart"
    echo "(podman restart <name>, or recreate the install)."
fi
'

journal_snippet() {
    printf '%s\n' "
set -e
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=%s\n' '$1' | sudo tee /etc/systemd/journald.conf.d/wip.conf >/dev/null
sudo systemctl restart systemd-journald
echo 'journal capped at $1 (SystemMaxUse)'
journalctl --disk-usage
"
}

run() {
    if [ "$DRY_RUN" = 1 ]; then
        echo "--- would run on the podman host:"
        printf '%s\n' "$1"
    else
        on_host "$1"
    fi
}

if [ "$MEASURE" = 1 ]; then
    run "$MEASURE_SNIPPET"
    exit 0
fi

echo "== before"
run "$MEASURE_SNIPPET"
echo "== podman service log level"
run "$LOG_LEVEL_SNIPPET"
if [ -n "$JOURNAL_MAX" ]; then
    echo "== journal bound"
    run "$(journal_snippet "$JOURNAL_MAX")"
fi
echo "== done. Measure again in 10 minutes or more: $0 --measure"
