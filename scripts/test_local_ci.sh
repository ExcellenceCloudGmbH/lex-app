#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────
#  Local CI emulator — runs the exact same test pipeline as GitHub
#  Actions, inside Docker containers with Postgres.
#
#  Usage:
#    ./scripts/test_local_ci.sh          # run tests + coverage
#    ./scripts/test_local_ci.sh --shell  # drop into the container
#    ./scripts/test_local_ci.sh --quick  # SimpleTestCase only (no DB)
# ──────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

NETWORK_NAME="lex-ci-net"
POSTGRES_CONTAINER="lex-ci-postgres"
TEST_CONTAINER="lex-ci-runner"
IMAGE_NAME="lex-ci-python"

# ── cleanup on exit ──────────────────────────────────────────────────
cleanup() {
    echo ""
    echo "Cleaning up..."
    docker rm -f "$TEST_CONTAINER" 2>/dev/null || true
    docker rm -f "$POSTGRES_CONTAINER" 2>/dev/null || true
    docker network rm "$NETWORK_NAME" 2>/dev/null || true
}
trap cleanup EXIT

# ── build the CI image locally (no Docker Hub needed) ────────────────
echo "==> Building CI image (cached after first run)..."
docker build -t "$IMAGE_NAME" -f "$SCRIPT_DIR/Dockerfile.ci" "$SCRIPT_DIR" 2>&1 | tail -3

# ── create network ───────────────────────────────────────────────────
docker network create "$NETWORK_NAME" 2>/dev/null || true

# ── start postgres (same as CI) ──────────────────────────────────────
echo "==> Starting Postgres..."
docker rm -f "$POSTGRES_CONTAINER" 2>/dev/null || true
docker run -d \
    --name "$POSTGRES_CONTAINER" \
    --network "$NETWORK_NAME" \
    -e POSTGRES_USER=django \
    -e POSTGRES_PASSWORD=lundadminlocal \
    -e POSTGRES_DB=db_lex \
    postgres:latest >/dev/null

# Wait for postgres to be ready
echo "==> Waiting for Postgres..."
for i in $(seq 1 30); do
    if docker exec "$POSTGRES_CONTAINER" pg_isready -U django >/dev/null 2>&1; then
        echo "==> Postgres ready"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "ERROR: Postgres did not start in time"
        exit 1
    fi
    sleep 1
done

# ── build test command ───────────────────────────────────────────────
TEST_CMD=$(cat <<'SCRIPT'
set -e

echo ""
echo "==> Installing dependencies..."
pip install --prefer-binary -r requirements.txt -q 2>&1 | tail -1
pip install -e . -q 2>&1 | tail -1

echo ""
echo "==> Running tests with coverage..."
export DJANGO_SETTINGS_MODULE=lex_app.settings
export DATABASE_DEPLOYMENT_TARGET=default
export CELERY_ACTIVE=False

# Postgres connection — matches the CI service container
export DATABASE_HOST=lex-ci-postgres
export DATABASE_PORT=5432
export DATABASE_USER=django
export DATABASE_PASSWORD=lundadminlocal
export DATABASE_NAME=db_lex

coverage run --rcfile=.coveragerc -m lex test --verbosity=2 --noinput \
    lex.core.tests \
    lex.audit_logging.tests \
    lex.process_admin.tests \
    lex.lex_app.tests \
    lex.tests

echo ""
echo "=========================================="
echo "  COVERAGE REPORT (informational only)"
echo "=========================================="
coverage report --rcfile=.coveragerc


coverage xml --rcfile=.coveragerc -o coverage.xml
echo "==> Coverage XML saved to coverage.xml"
SCRIPT
)

# ── quick mode (no DB, just SimpleTestCase) ──────────────────────────
QUICK_CMD=$(cat <<'SCRIPT'
set -e

echo ""
echo "==> Installing dependencies..."
pip install --prefer-binary -r requirements.txt -q 2>&1 | tail -1
pip install -e . -q 2>&1 | tail -1

echo ""
echo "==> Running SimpleTestCase tests (no DB)..."
export DJANGO_SETTINGS_MODULE=lex_app.settings
export CELERY_ACTIVE=False
export DATABASE_DEPLOYMENT_TARGET=default
export DATABASE_HOST=lex-ci-postgres
export DATABASE_PORT=5432
export DATABASE_USER=django
export DATABASE_PASSWORD=lundadminlocal
export DATABASE_NAME=db_lex

python -m lex test --verbosity=2 --noinput \
    lex.tests.test_legacy_audit_payload \
    lex.tests.test_audit_data_models \
    lex.tests.test_model_context \
    lex.tests.test_runtime_config \
    lex.tests.test_cache_manager \
    lex.tests.test_auth_logout \
    lex.tests.test_pagination \
    lex.tests.test_injector_decorator \
    lex.tests.test_custom_storage \
    lex.tests.test_model_converter \
    lex.tests.test_channel_layer_utils \
    lex.tests.test_audit_config \
    lex.tests.test_generic_app_config_helpers \
    lex.tests.test_calculation_audit_helpers \
    lex.tests.test_token_context \
    lex.tests.test_objects_to_recalculate_store \
    lex.tests.test_singleton_decorator \
    lex.core.tests.test_exceptions \
    lex.tests.test_temporal_utils \
    lex.tests.test_view_utils \
    lex.tests.test_serializer_helpers \
    lex.core.tests.test_active_calculation_state_store \
    lex.tests.test_collection_utils \
    lex.tests.test_api_helpers \
    lex.tests.test_bitemporal_suppress_context_managers \
    lex.tests.test_api_key_requests \
    lex.tests.test_generic_filters \
    lex.tests.test_operation_context \
    lex.tests.test_keycloak_middleware \
    lex.core.tests.test_calculation_model_helpers \
    lex.tests.test_serializer_parse_value \
    lex.process_admin.tests.test_model_utils \
    lex.process_admin.tests.test_constants

echo ""
echo "PASS: All SimpleTestCase tests passed"
SCRIPT
)

# ── run tests ────────────────────────────────────────────────────────
echo ""
echo "==> Starting test runner..."

if [[ "${1:-}" == "--shell" ]]; then
    docker run -it --rm \
        --name "$TEST_CONTAINER" \
        --network "$NETWORK_NAME" \
        -v "$PROJECT_ROOT:/app" \
        -w /app \
        "$IMAGE_NAME" \
        bash
elif [[ "${1:-}" == "--quick" ]]; then
    docker run --rm \
        --name "$TEST_CONTAINER" \
        --network "$NETWORK_NAME" \
        -v "$PROJECT_ROOT:/app" \
        -w /app \
        "$IMAGE_NAME" \
        bash -c "$QUICK_CMD"
else
    docker run --rm \
        --name "$TEST_CONTAINER" \
        --network "$NETWORK_NAME" \
        -v "$PROJECT_ROOT:/app" \
        -w /app \
        "$IMAGE_NAME" \
        bash -c "$TEST_CMD"
fi
