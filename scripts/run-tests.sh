#!/bin/bash
# Run all tests locally
# Usage: ./scripts/run-tests.sh [backend|frontend|all]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

run_backend_tests() {
    echo -e "${YELLOW}=== Running Backend Tests ===${NC}"

    for component in registry def-store template-store document-store reporting-sync; do
        component_dir="$PROJECT_ROOT/components/$component"
        if [ -d "$component_dir/tests" ]; then
            echo -e "\n${YELLOW}Testing: $component${NC}"

            # Check if running in container or locally
            if podman ps --filter name="wip-${component}-dev" --format "{{.Names}}" | grep -q .; then
                # Run in container
                podman exec -it "wip-${component//-/}-dev" bash -c \
                    "cd /app && pip install -q pytest pytest-asyncio httpx && PYTHONPATH=/app/src pytest tests/ -v --tb=short" \
                    && echo -e "${GREEN}$component: PASSED${NC}" \
                    || echo -e "${RED}$component: FAILED${NC}"
            else
                echo -e "${YELLOW}Container not running, skipping $component${NC}"
            fi
        fi
    done

    # wip-auth library
    echo -e "\n${YELLOW}Testing: wip-auth${NC}"
    if [ -d "$PROJECT_ROOT/libs/wip-auth/tests" ]; then
        cd "$PROJECT_ROOT/libs/wip-auth"
        pip install -q -e . 2>/dev/null || true
        pip install -q pytest pytest-asyncio httpx 2>/dev/null || true
        python -m pytest tests/ -v --tb=short \
            && echo -e "${GREEN}wip-auth: PASSED${NC}" \
            || echo -e "${RED}wip-auth: FAILED${NC}"
    fi
}

run_frontend_tests() {
    echo -e "${YELLOW}=== Running Frontend Tests ===${NC}"

    cd "$PROJECT_ROOT/ui/wip-console"

    # Check if node_modules exists
    if [ ! -d "node_modules" ]; then
        echo "Installing dependencies..."
        npm install
    fi

    # Check if vitest is installed
    if ! npm list vitest >/dev/null 2>&1; then
        echo "Installing test dependencies..."
        npm install
    fi

    echo -e "\n${YELLOW}Type checking...${NC}"
    npm run type-check && echo -e "${GREEN}Type check: PASSED${NC}" || echo -e "${RED}Type check: FAILED${NC}"

    echo -e "\n${YELLOW}Running tests...${NC}"
    npm run test:run && echo -e "${GREEN}Tests: PASSED${NC}" || echo -e "${RED}Tests: FAILED${NC}"
}

case "${1:-all}" in
    backend)
        run_backend_tests
        ;;
    frontend)
        run_frontend_tests
        ;;
    all)
        run_backend_tests
        echo ""
        run_frontend_tests
        ;;
    *)
        echo "Usage: $0 [backend|frontend|all]"
        exit 1
        ;;
esac

echo -e "\n${GREEN}=== Test run complete ===${NC}"
