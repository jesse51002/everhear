#!/usr/bin/env bash
# After compaction: hand the conversation the last 15 minutes back.
echo "The conversation was just compacted. Lines from the last 15 minutes:"
timeout 10 vfg history --since -15m --limit 60 2>&1 || echo "(vfg history failed)"
echo "Game events from the last 15 minutes:"
timeout 10 vfg history --events --since -15m --limit 30 2>&1 || echo "(vfg history --events failed)"
echo "Your watch on vfg watch is still running: do not arm another; re-arm only when its end notice arrives."
exit 0
