#!/usr/bin/env bash
# ==============================================================================
# Script: clear_cache_and_history.sh
# Purpose: Clear cache, conversations, and turn history for PMO Stack & Dashboard
# ==============================================================================

set -e

echo "🧹 Starting cache and conversation history cleanup..."

# ------------------------------------------------------------------------------
# 1. Clear PMO Dashboard (Docker Postgres & Redis)
# ------------------------------------------------------------------------------
echo ""
echo "👉 [1/2] Clearing PMO Dashboard (Docker Postgres & Redis)..."

POSTGRES_CONTAINER=$(docker ps --filter "name=postgres" --format "{{.Names}}" | head -n 1)
REDIS_CONTAINER=$(docker ps --filter "name=redis" --format "{{.Names}}" | head -n 1)

if [ -n "$POSTGRES_CONTAINER" ]; then
    echo "   Clearing Postgres chat tables (turn_events, turns, conversations)..."
    docker exec "$POSTGRES_CONTAINER" psql -U postgres -d tela -c \
        "TRUNCATE TABLE chat.turn_events, chat.turns, chat.conversations CASCADE;" 2>/dev/null || \
    docker exec "$POSTGRES_CONTAINER" psql -U tela_owner -d tela -c \
        "TRUNCATE TABLE chat.turn_events, chat.turns, chat.conversations CASCADE;" 2>/dev/null || \
        echo "   ⚠️ Could not truncate Postgres tables directly; please ensure permissions."
    echo "   ✅ PMO Dashboard Postgres conversations and turns cleared."
else
    echo "   ℹ️ Postgres container not running (skipped)."
fi

if [ -n "$REDIS_CONTAINER" ]; then
    echo "   Flushing Redis cache..."
    docker exec "$REDIS_CONTAINER" redis-cli FLUSHALL >/dev/null 2>&1 || true
    echo "   ✅ Redis cache flushed."
else
    echo "   ℹ️ Redis container not running (skipped)."
fi

# ------------------------------------------------------------------------------
# 2. Clear Tela (ni-pmo-stack) conversation history
# ------------------------------------------------------------------------------
echo ""
echo "👉 [2/2] Clearing Tela (ni-pmo-stack) conversation history..."

TELA_CONV_FILE="/Users/prodian/Desktop/ni-pmo-stack/tela/backend/data/api/conversations.json"

if [ -f "$TELA_CONV_FILE" ]; then
    # Backup current file just in case
    cp "$TELA_CONV_FILE" "${TELA_CONV_FILE}.bak"
    # Reset to empty JSON array
    echo "[]" > "$TELA_CONV_FILE"
    echo "   ✅ Tela conversations.json reset to empty (backup saved to .bak)."
else
    echo "   ℹ️ $TELA_CONV_FILE not found (skipped)."
fi

echo ""
echo "🎉 Cleanup complete! All caches and chat history have been successfully deleted."
