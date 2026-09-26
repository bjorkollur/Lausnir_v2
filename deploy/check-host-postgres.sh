#!/bin/bash
# Check whether a host Mac already runs PostgreSQL in a way that would clash
# with the RuleOfLaw drive's instance. Run this BEFORE setting the drive up.
#
#   bash /Volumes/RuleOfLaw/Lausnir/deploy/check-host-postgres.sh
#
# Read-only: reports, changes nothing. Distinguishes our own instance (data
# directory on the drive) from a foreign one, so it is also safe to run after
# setup as a health check.

OURS=/Volumes/RuleOfLaw/postgresql_data
warn=0

echo "=== 1. PostgreSQL versions installed via Homebrew ==="
if command -v brew >/dev/null 2>&1; then
    found=$(brew list --formula 2>/dev/null | grep -E '^postgresql(@[0-9]+)?$')
    if [ -z "$found" ]; then
        echo "  none — install postgresql@17 (our data directory is 17.x)"; warn=1
    else
        echo "$found" | sed 's/^/  /'
        echo "$found" | grep -q '^postgresql@17$' || {
            echo "  [!] postgresql@17 is NOT installed — required, our data dir is 17.x"; warn=1; }
    fi
else
    echo "  Homebrew not installed"; warn=1
fi

echo
echo "=== 2. Running postgres processes ==="
# pgrep -x, not `ps | grep`: this script's own command line contains the
# pattern and would otherwise match itself.
if pgrep -x postgres >/dev/null 2>&1; then
    foreign=0
    for pid in $(pgrep -x postgres); do
        cmd=$(ps -p "$pid" -o command= 2>/dev/null)
        case "$cmd" in
            *"-D $OURS"*) echo "  [ours]    $cmd" ;;
            *)            echo "  [foreign] $cmd"; foreign=1 ;;
        esac
    done
    [ "$foreign" -eq 1 ] && echo "  note: a foreign instance is fine as long as it is not on port 5433"
else
    echo "  none running"
fi

echo
echo "=== 3. Host postgres under brew services ==="
# Since 2026-09-23 our service uses its own label (is.lausnir.postgres), so
# brew services cannot touch it. A host instance here is informational only.
svc=$(brew services list 2>/dev/null | grep -i postgres)
if [ -z "$svc" ]; then
    echo "  none"
else
    echo "$svc" | sed 's/^/  /'
    echo "  -> harmless: our service uses the label 'is.lausnir.postgres',"
    echo "     which brew services never rewrites."
fi

echo
echo "=== 4. Ports ==="
for p in 5432 5433; do
    holder=$(lsof -iTCP:$p -sTCP:LISTEN -P -t 2>/dev/null | head -1)
    if [ -z "$holder" ]; then
        echo "  $p: free"
    else
        cmd=$(ps -p "$holder" -o command= 2>/dev/null)
        if echo "$cmd" | grep -q -- "-D $OURS"; then
            echo "  $p: held by OUR instance"
        else
            echo "  $p: held by pid $holder — $(echo "$cmd" | cut -c1-60)"
            [ "$p" = "5433" ] && { echo "      [!] 5433 is our port and something else holds it"; warn=1; }
        fi
    fi
done

echo
echo "=== 5. Our launchd job (is.lausnir.postgres) ==="
job=$(launchctl list 2>/dev/null | grep 'is.lausnir.postgres')
if [ -z "$job" ]; then
    echo "  not loaded — free to bootstrap ours (doc steps 7 and 9)"
else
    echo "$job" | sed 's/^/  /'
    code=$(echo "$job" | awk '{print $2}')
    case "$code" in
        0) echo "  -> running correctly" ;;
        78) echo "  [!] exit 78: launchd could not open the log file — it must live"
            echo "      on the internal disk, not the drive (see the plist comment)"; warn=1 ;;
        *)  echo "  [!] last exit code $code — check /opt/homebrew/var/log/lausnir-postgres.log"; warn=1 ;;
    esac
fi

echo
echo "=== Niðurstaða ==="
if [ "$warn" -eq 0 ]; then
    echo "  Engir árekstrar. Óhætt að halda áfram."
else
    echo "  [!] Sjá viðvaranir að ofan og docs/postgres-external-drive-setup.md."
fi
echo "  (Postgres hýsilvélarinnar á porti 5432 er í lagi — við notum 5433.)"
exit $warn
