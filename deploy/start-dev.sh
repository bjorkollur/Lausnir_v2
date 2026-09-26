#!/bin/bash
# Start (or stop) the Lausnir development stack: Postgres on 5433, the API on
# 8077 and the Vite frontend on 5173.
#
#   bash /Volumes/RuleOfLaw/Lausnir/deploy/start-dev.sh [start|stop|status]
#
# Safe to run twice: a service already listening on its port is left alone
# rather than started a second time. API and frontend run detached with nohup,
# so they survive the terminal closing; logs go to /tmp/lausnir-dev/.
#
# Postgres is deliberately NOT stopped by `stop` — it is a launchd service that
# belongs to the machine, not to a dev session. Unload it with
#   launchctl bootout gui/$(id -u)/is.lausnir.postgres
# before unmounting the drive.

# Homebrew's bin may be missing from a non-interactive PATH (uv, npm, psql).
export PATH="/opt/homebrew/bin:$PATH"

ROOT=/Volumes/RuleOfLaw/Lausnir
DRIVE=/Volumes/RuleOfLaw
PLIST=~/Library/LaunchAgents/is.lausnir.postgres.plist
RUN_DIR=/tmp/lausnir-dev

PG_PORT=5433
API_PORT=8077
WEB_PORT=5173

mkdir -p "$RUN_DIR"

# ── helpers ──────────────────────────────────────────────────────────────────

_listening() {  # $1 = port — succeeds if anything is listening on it
    lsof -iTCP:"$1" -sTCP:LISTEN -P -t >/dev/null 2>&1
}

_kill_tree() {  # $1 = pid — kill children before the parent
    local pid=$1 child
    [ -z "$pid" ] && return
    for child in $(pgrep -P "$pid" 2>/dev/null); do _kill_tree "$child"; done
    kill "$pid" 2>/dev/null
}

_wait_for() {   # $1 = seconds, $2... = command that succeeds once ready
    local deadline=$(( SECONDS + $1 )); shift
    while [ "$SECONDS" -lt "$deadline" ]; do
        "$@" >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

# ── stop ─────────────────────────────────────────────────────────────────────

_port_free() {  # $1 = port, $2 = seconds — kill is async, so give it time
    local deadline=$(( SECONDS + $2 ))
    while [ "$SECONDS" -lt "$deadline" ]; do
        _listening "$1" || return 0
        sleep 1
    done
    _listening "$1" && return 1 || return 0
}

_stop_one() {   # $1 = label, $2 = pidfile, $3 = port
    local label=$1 pidfile=$2 port=$3 pid
    if [ -f "$pidfile" ]; then
        pid=$(cat "$pidfile" 2>/dev/null)
        _kill_tree "$pid"
        rm -f "$pidfile"
    fi
    _port_free "$port" 5 && { echo "  $label stöðvað"; return 0; }

    # Still listening: started by hand (no pidfile), or a uvicorn --reload
    # worker that outlived the parent we just killed.
    lsof -iTCP:"$port" -sTCP:LISTEN -P -t 2>/dev/null | xargs kill 2>/dev/null
    _port_free "$port" 5 && { echo "  $label stöðvað"; return 0; }

    lsof -iTCP:"$port" -sTCP:LISTEN -P -t 2>/dev/null | xargs kill -9 2>/dev/null
    _port_free "$port" 5 && { echo "  $label stöðvað (SIGKILL)"; return 0; }

    echo "  [!] $label: eitthvað hlustar enn á $port"
    return 1
}

if [ "$1" = "stop" ]; then
    echo "=== Stöðva ==="
    _stop_one "API" "$RUN_DIR/api.pid" "$API_PORT"
    _stop_one "Framendi" "$RUN_DIR/web.pid" "$WEB_PORT"
    echo
    echo "Postgres keyrir áfram (launchd). Áður en diskurinn er aftengdur:"
    echo "  launchctl bootout gui/\$(id -u)/is.lausnir.postgres"
    exit 0
fi

# ── status ───────────────────────────────────────────────────────────────────

if [ "$1" = "status" ]; then
    echo "=== Staða ==="
    pg_isready -h localhost -p "$PG_PORT" -q 2>/dev/null \
        && echo "  Postgres   :$PG_PORT  í gangi" \
        || echo "  Postgres   :$PG_PORT  EKKI í gangi"
    _listening "$API_PORT" \
        && echo "  API        :$API_PORT  í gangi" \
        || echo "  API        :$API_PORT  EKKI í gangi"
    _listening "$WEB_PORT" \
        && echo "  Framendi   :$WEB_PORT  í gangi" \
        || echo "  Framendi   :$WEB_PORT  EKKI í gangi"
    exit 0
fi

# ── start ────────────────────────────────────────────────────────────────────

if [ ! -d "$ROOT" ]; then
    echo "[!] $DRIVE er ekki tengdur — tengdu diskinn fyrst."
    exit 1
fi

echo "=== 1. Postgres (:$PG_PORT) ==="
if pg_isready -h localhost -p "$PG_PORT" -q 2>/dev/null; then
    echo "  þegar í gangi"
else
    if [ ! -f "$PLIST" ]; then
        echo "  [!] $PLIST vantar — sjá UPPSETNING-NY-VEL.md skref 3"
        exit 1
    fi
    echo "  ræsi launchd-þjónustuna..."
    launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null
    if _wait_for 30 pg_isready -h localhost -p "$PG_PORT" -q; then
        echo "  í gangi"
    else
        # launchctl reports status 0 as soon as it spawns the process, so a
        # silent hang here is almost always the missing Full Disk Access for
        # /opt/homebrew/opt/postgresql@17/bin/postgres (setup doc step 2).
        echo "  [!] svarar ekki eftir 30s — líklega vantar Full Disk Access"
        echo "      sjá /opt/homebrew/var/log/lausnir-postgres.log"
        exit 1
    fi
fi

echo
echo "=== 2. API (:$API_PORT) ==="
if _listening "$API_PORT"; then
    echo "  þegar í gangi"
else
    if [ ! -f "$ROOT/.env" ]; then
        echo "  [!] $ROOT/.env vantar (DATABASE_URL, DATA_DIR)"
        exit 1
    fi
    cd "$ROOT" || exit 1
    set -a; . ./.env; set +a
    nohup uv run uvicorn engine.api.app:app --reload --port "$API_PORT" \
        > "$RUN_DIR/api.log" 2>&1 &
    echo $! > "$RUN_DIR/api.pid"
    if _wait_for 60 curl -sf "http://localhost:$API_PORT/api/health"; then
        echo "  í gangi — log: $RUN_DIR/api.log"
    else
        echo "  [!] svaraði ekki /api/health eftir 60s — sjá $RUN_DIR/api.log"
        tail -5 "$RUN_DIR/api.log" | sed 's/^/      /'
        exit 1
    fi
fi

echo
echo "=== 3. Framendi (:$WEB_PORT) ==="
# The frontend talks to the API over an absolute URL from VITE_API_BASE, so a
# stale .env.local — one carried over from another machine, pointing at that
# machine's LAN address — loads fine and then silently fetches nothing.
if [ -f "$ROOT/frontend/.env.local" ] \
   && ! grep -q "localhost:$API_PORT" "$ROOT/frontend/.env.local"; then
    echo "  [!] frontend/.env.local bendir ekki á localhost:$API_PORT:"
    grep VITE_API_BASE "$ROOT/frontend/.env.local" | sed 's/^/      /'
    echo "      Vite tekur .env.local fram yfir .env — færðu hana til hliðar"
    echo "      ef framendinn finnur engin gögn."
fi
if _listening "$WEB_PORT"; then
    echo "  þegar í gangi"
else
    if [ ! -d "$ROOT/frontend/node_modules" ]; then
        echo "  [!] node_modules vantar — keyrðu: cd $ROOT/frontend && npm install"
        exit 1
    fi
    cd "$ROOT/frontend" || exit 1
    nohup npm run dev > "$RUN_DIR/web.log" 2>&1 &
    echo $! > "$RUN_DIR/web.pid"
    if _wait_for 60 curl -sf "http://localhost:$WEB_PORT/"; then
        echo "  í gangi — log: $RUN_DIR/web.log"
    else
        echo "  [!] svaraði ekki eftir 60s — sjá $RUN_DIR/web.log"
        tail -5 "$RUN_DIR/web.log" | sed 's/^/      /'
        exit 1
    fi
fi

echo
echo "=== Tilbúið ==="
echo "  Framendi   http://localhost:$WEB_PORT"
echo "  API        http://localhost:$API_PORT/api/health"
echo "  Stöðva     bash $ROOT/deploy/start-dev.sh stop"
