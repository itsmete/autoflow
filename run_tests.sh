#!/usr/bin/env bash
#
# AutoFlow test runner.
#
#   ./run_tests.sh                    # whole suite, fakeredis, no services needed
#   ./run_tests.sh tests.test_core    # one module / class / method
#   ./run_tests.sh --redis            # same suite against a real redis in docker
#
# The suite needs no Redis, no Celery worker and no Postgres: it runs on SQLite and
# a fakeredis instance that executes the real lua scripts. --redis swaps in a real
# server, which is worth doing before trusting the cache layer in production.

set -euo pipefail

cd "$(dirname "$0")"

VENV=./venv
PYTHON="$VENV/bin/python"

if [ ! -x "$PYTHON" ]; then
        echo "==> creating $VENV"
        python3 -m venv "$VENV"
        "$VENV/bin/pip" install --quiet --upgrade pip
        "$VENV/bin/pip" install --quiet -r requirements-dev.txt
fi

# Verify the dev extras are present; requirements-dev.txt may have grown since the
# venv was built.
if ! "$PYTHON" -c "import fakeredis" >/dev/null 2>&1; then
        echo "==> installing dev requirements"
        "$VENV/bin/pip" install --quiet -r requirements-dev.txt
fi

USE_REAL_REDIS=0
ARGS=()
for arg in "$@"; do
        case "$arg" in
                --redis) USE_REAL_REDIS=1 ;;
                *) ARGS+=("$arg") ;;
        esac
done

if [ "$USE_REAL_REDIS" = "1" ]; then
        CONTAINER=autoflow-test-redis
        PORT=6399

        # The test database is FLUSHDB'd between tests, hence a throwaway container
        # rather than whatever the developer happens to be running on 6379.
        if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
                echo "==> starting ${CONTAINER} on :${PORT}"
                docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
                docker run -d --name "$CONTAINER" -p "${PORT}:6379" redis:7-alpine >/dev/null
                sleep 2
        fi

        export AUTOFLOW_TEST_REDIS_URL="redis://localhost:${PORT}/0"
        echo "==> running against real redis at $AUTOFLOW_TEST_REDIS_URL"
fi

if [ ${#ARGS[@]} -eq 0 ]; then
        ARGS=(tests)
fi

exec "$PYTHON" manage.py test "${ARGS[@]}"
