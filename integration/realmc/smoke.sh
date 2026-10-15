#!/usr/bin/env bash
# The realmc smoke test, against a real 26.3 server: up (its world fresh from the template), RCON
# (realmc.py cmd), vfg's read-only `vfg state` through the stand-in, a fill checked with `execute if blocks`, a fake player (with Carpet) that turns,
# right-clicks a carrot_on_a_stick and sneaks; then the game engine on it: its venv and datapack
# installed as on the box (engine/deploy/install.sh), and one live test of a tiny ability run by
# python -m vfg_engine.livetest (engine/tests/box/livetest_case); down. PASS or FAIL per check;
# exit 1 if any failed.
#
#   integration/realmc/smoke.sh [NAME] [--keep]    NAME defaults to smoke; --keep leaves it running
set -uo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
name=smoke
keep=0
for arg in "$@"; do
  case $arg in
    --keep) keep=1 ;;
    -*) echo "usage: smoke.sh [NAME] [--keep]" >&2; exit 2 ;;
    *) name=$arg ;;
  esac
done
fails=0
ok() { printf '  PASS  %s\n' "$1"; }
bad() { printf '  FAIL  %s\n          %s\n' "$1" "$2"; fails=$((fails + 1)); }
expect() {  # expect <label> <regex> <output>
  if [[ $3 =~ $2 ]]; then ok "$1: $3"; else bad "$1" "got: $3"; fi
}

echo "== up $name --fresh"
t0=$(date +%s.%N)
if ! "$here/up" "$name" --fresh; then
  echo "  FAIL  up"
  exit 1
fi
printf '  PASS  up in %s s, wall clock\n' "$(awk "BEGIN { printf \"%.1f\", $(date +%s.%N) - $t0 }")"
eval "$("$here/env" "$name")"
[[ $(command -v vfg) == "$here/bin/vfg" ]] && ok "vfg is the stand-in's" || bad "vfg is the stand-in's" "$(command -v vfg)"

read -r ax ay az mode < <(python3 "$here/realmc.py" status "$name" --json | python3 -c '
import json, sys; s = json.load(sys.stdin); a = s["arena"]; print(a["x"], a["y"], a["z"], s["mode"])')
# Straight to RCON: `vfg cmd` goes through the engine's command scenario, which the engine section
# below tests with the rest of the engine.
rc() { python3 "$here/realmc.py" cmd "$1" 2>&1; }
echo "== RCON (arena at $ax $ay $az, $mode)"
expect "time query day (26.3's form of daytime)" '^Timeline minecraft:day is at 6000 tick' "$(rc 'time query day')"
expect "list" '^There are [0-9]+ of a max of [0-9]+ players online' "$(rc list)"

echo "== fill, then execute if blocks"
x=$((ax + 4)) y=$ay z=$((az + 4))
expect "fill 3x3x3 gold" '^Successfully filled 27 block' "$(rc "fill $x $y $z $((x+2)) $((y+2)) $((z+2)) minecraft:gold_block")"
expect "fill its twin" '^Successfully filled 27 block' "$(rc "fill $((x+4)) $y $z $((x+6)) $((y+2)) $((z+2)) minecraft:gold_block")"
expect "the two match" '^Test passed\. Count: 27$' "$(rc "execute if blocks $x $y $z $((x+2)) $((y+2)) $((z+2)) $((x+4)) $y $z all")"
rc "setblock $((x+5)) $((y+1)) $((z+1)) minecraft:stone" >/dev/null
expect "one block changed: they differ" '^Test failed' "$(rc "execute if blocks $x $y $z $((x+2)) $((y+2)) $((z+2)) $((x+4)) $y $z all")"
expect "that block is stone" '^Test passed$' "$(rc "execute if block $((x+5)) $((y+1)) $((z+1)) minecraft:stone")"
rc "fill $x $y $z $((x+6)) $((y+2)) $((z+2)) minecraft:air" >/dev/null

players() { python3 "$here/players.py" --name "$name" "$@" 2>&1; }
if [[ $mode == carpet ]]; then
  echo "== a fake player (Carpet)"
  score() { rc 'scoreboard players get Steve vfg_click' | sed -n 's/^Steve has \([0-9]*\) .*/\1/p'; }
  expect "spawn Steve on the arena" "^Steve at " "$(players spawn Steve)"
  expect "he is in @a" '^Test passed' "$(rc 'execute if entity @a[name=Steve]')"
  expect "in survival, not Carpet's creative" 'entity data: 0$' "$(rc 'data get entity Steve playerGameType')"
  expect "vfg state shows where he is" 'Steve 0 85 0 overworld' "$(vfg state 2>&1 | grep '^online')"
  players look Steve 90 -30 >/dev/null
  expect "look 90 -30, read back from Rotation" 'yaw 90.0 pitch -30.0$' "$(players pos Steve)"
  players look-at Steve 10.5 85 0.5 >/dev/null
  expect "look at a point east of him: yaw -90" 'yaw -90.0 pitch' "$(players pos Steve)"
  rc "scoreboard objectives add vfg_click minecraft.used:minecraft.carrot_on_a_stick" >/dev/null
  players give Steve 'carrot_on_a_stick[custom_data={vfg_item:"smoke"},item_name="Smoke"]' >/dev/null
  expect "he holds it, custom_data and all" '^Test passed' \
    "$(rc 'execute if items entity Steve weapon.mainhand *[custom_data~{vfg_item:"smoke"}]')"
  c0=$(score); c0=${c0:-0}
  players use Steve >/dev/null
  sleep 0.5
  c1=$(score)
  [[ $c1 == $((c0 + 1)) ]] && ok "one use: vfg_click (used:carrot_on_a_stick) $c0 -> $c1" || bad "one use" "vfg_click $c0 -> $c1"
  players use Steve continuous >/dev/null
  sleep 1
  players use Steve >/dev/null
  sleep 0.3
  c2=$(score)
  sleep 1
  c3=$(score)
  (( c2 > c1 + 5 )) && [[ $c3 == "$c2" ]] && ok "use continuous, then use stops it: $c1 -> $c2, still $c3" \
    || bad "use continuous, then use" "$c1 -> $c2 -> $c3"
  players hold Steve >/dev/null
  sleep 1
  players stop Steve >/dev/null
  sleep 0.3
  c4=$(score)
  (( c4 - c3 >= 3 && c4 - c3 <= 6 )) && ok "hold 1 s (one use every 4 ticks): +$((c4 - c3))" || bad "hold 1 s" "+$((c4 - c3))"
  sneaking='execute as Steve if predicate {type:"minecraft:entity_properties",entity:"this",predicate:{flags:{is_sneaking:true}}}'
  players sneak Steve on >/dev/null
  sleep 0.3
  expect "sneak: he is sneaking" '^Test passed' "$(rc "$sneaking")"
  players sneak Steve off >/dev/null
  sleep 0.3
  out=$(rc "$sneaking")
  [[ $out != *passed* ]] && ok "unsneak: he is not" || bad "unsneak" "$out"
  players look Steve 90 0 >/dev/null
  before=$(players pos Steve)
  players move Steve forward >/dev/null
  sleep 1
  players move Steve stop >/dev/null
  sleep 0.5
  after=$(players pos Steve)
  sleep 0.5
  still=$(players pos Steve)
  moved=$(python3 -c 'import sys; a, b = [list(map(float, s.split()[:3])) for s in sys.argv[1:3]]; print("%.2f %.2f" % (b[0] - a[0], b[2] - a[2]))' "$before" "$after")
  read -r dx dz <<<"$moved"
  if awk "BEGIN { exit !($dx < -2 && $dz < 0.5 && $dz > -0.5) }" && [[ $after == "$still" ]]; then
    ok "move forward 1 s at yaw 90: $dx blocks west, then stop holds him still"
  else bad "move forward / stop" "$before -> $after -> $still"; fi
  y0=$(players pos Steve | cut -d' ' -f2); players jump Steve >/dev/null; sleep 0.15
  y1=$(players pos Steve | cut -d' ' -f2); sleep 1; y2=$(players pos Steve | cut -d' ' -f2)
  awk "BEGIN { exit !($y1 > $y0 + 0.5 && $y2 == $y0) }" && ok "jump: y $y0 -> $y1 -> $y2" || bad "jump" "y $y0 -> $y1 -> $y2"
  players drop Steve >/dev/null
  sleep 0.5
  expect "drop: the item lies by him" '^Test passed' "$(rc 'execute at Steve if entity @e[type=minecraft:item,distance=..6]')"
  expect "drop: his hand is empty" '^Test failed' "$(rc 'execute if items entity Steve weapon.mainhand *[custom_data~{vfg_item:"smoke"}]')"
  rc "kill @e[type=minecraft:item]" >/dev/null
  players spawn Steve >/dev/null   # back on the arena's centre for what follows
else
  echo "== fake players: skipped (no Carpet; integration/realmc/realmc.py carpet, then run this again)"
fi

if [[ $mode == carpet ]]; then
  players kill Steve >/dev/null
  expect "kill: he has left" '^There are 0 of a max' "$(rc list)"

  echo "== the game engine: installed as on the box, then a live test of a tiny ability"
  repo=$(dirname "$(dirname "$here")")
  e=$REALMC_DIR/vfg/engine
  mkdir -p "$e/datapack" "$e/deploy"
  rsync -r --delete "$repo/engine/datapack/everhear" "$e/datapack/"
  cp "$repo/engine/deploy/install.sh" "$repo/engine/deploy/requirements.txt" "$e/deploy/"
  cp "$repo/bridge/deploy/rcon.py" "$REALMC_DIR/mc/rcon.py"
  if out=$(VFG_DIR="$REALMC_DIR/vfg" MC_DIR="$REALMC_DIR/mc" UV=$(command -v uv) bash "$e/deploy/install.sh" 2>&1); then
    ok "install.sh: $(printf '%s' "$out" | tail -n 1)"
  else bad "install.sh" "$out"; fi
  # The livetest runner talks only to a test server: this one is the smoke test's own, marked for
  # the run and unmarked after.
  marker=$REALMC_DIR/mc/.vfg-test-server
  port=$(sed -n 's/^rcon.port=//p' "$REALMC_DIR/mc/server.properties")
  printf '{"made": "smoke.sh", "rcon_port": %s}\n' "$port" > "$marker"
  state=$(mktemp -d)
  case_dir=$repo/engine/tests/box/livetest_case
  out=$(PYTHONPATH="$repo/engine" "$e/.venv/bin/python" -m vfg_engine.livetest \
    --properties "$REALMC_DIR/mc/server.properties" --state-dir "$state" --content "$case_dir/content" \
    --test "$case_dir/zap_case.py" 2>&1)
  code=$?
  rm -f "$marker"
  rm -rf "$state"
  expect "livetest: test_zap (a cast through the engine hurts the husk)" 'RESULT: 6 passed, 0 failed' \
    "$(printf '%s' "$out" | grep '^RESULT' || printf '%s' "$out" | tail -n 3)"
  [[ $code == 0 ]] && ok "livetest exit 0: $(printf '%s' "$out" | grep '^TIME')" || bad "livetest exit" "$code"
else
  echo "== the game engine: skipped (no Carpet: its live test needs fake players)"
fi

if (( keep )); then
  echo "== kept running: eval \"\$($here/env $name)\""
else
  echo "== down $name"
  "$here/down" "$name" && ok "down" || bad "down" "exit $?"
fi
if (( fails )); then echo "SMOKE FAIL: $fails check(s)"; exit 1; fi
echo "SMOKE PASS"
