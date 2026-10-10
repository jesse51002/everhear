#!/usr/bin/env bash
# The realmc smoke test, against a real 26.3 server: up (its world fresh from the template), one
# command through vfg (the command action), a fill checked with `execute if blocks`, a fake
# player (with Carpet) that turns, right-clicks a carrot_on_a_stick and sneaks, one action run as
# a copy for a few seconds and stopped, down. PASS or FAIL per check; exit 1 if any failed.
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
echo "== vfg cmd (arena at $ax $ay $az, $mode)"
expect "time query day (26.3's form of daytime)" '^Timeline minecraft:day is at 6000 tick' "$(vfg cmd 'time query day' 2>&1)"
expect "list" '^There are [0-9]+ of a max of [0-9]+ players online' "$(vfg cmd list 2>&1)"

echo "== fill, then execute if blocks"
# The command action refuses what places or removes blocks (building is a script's), so the
# test's own fills go straight to RCON.
rc() { python3 "$here/realmc.py" cmd "$1" 2>&1; }
expect "vfg cmd refuses a fill" 'fill places or removes blocks' "$(vfg cmd "fill $ax $ay $az $ax $ay $az minecraft:stone" 2>&1)"
x=$((ax + 4)) y=$ay z=$((az + 4))
expect "fill 3x3x3 gold" '^Successfully filled 27 block' "$(rc "fill $x $y $z $((x+2)) $((y+2)) $((z+2)) minecraft:gold_block")"
expect "fill its twin" '^Successfully filled 27 block' "$(rc "fill $((x+4)) $y $z $((x+6)) $((y+2)) $((z+2)) minecraft:gold_block")"
expect "the two match" '^Test passed\. Count: 27$' "$(vfg cmd "execute if blocks $x $y $z $((x+2)) $((y+2)) $((z+2)) $((x+4)) $y $z all" 2>&1)"
rc "setblock $((x+5)) $((y+1)) $((z+1)) minecraft:stone" >/dev/null
expect "one block changed: they differ" '^Test failed' "$(vfg cmd "execute if blocks $x $y $z $((x+2)) $((y+2)) $((z+2)) $((x+4)) $y $z all" 2>&1)"
expect "that block is stone" '^Test passed$' "$(vfg cmd "execute if block $((x+5)) $((y+1)) $((z+1)) minecraft:stone" 2>&1)"
rc "fill $x $y $z $((x+6)) $((y+2)) $((z+2)) minecraft:air" >/dev/null

players() { python3 "$here/players.py" --name "$name" "$@" 2>&1; }
if [[ $mode == carpet ]]; then
  echo "== a fake player (Carpet)"
  score() { vfg cmd 'scoreboard players get Steve vfg_click' 2>&1 | sed -n 's/^Steve has \([0-9]*\) .*/\1/p'; }
  expect "spawn Steve on the arena" "^Steve at " "$(players spawn Steve)"
  expect "he is in @a" '^Test passed' "$(vfg cmd 'execute if entity @a[name=Steve]' 2>&1)"
  expect "in survival, not Carpet's creative" 'entity data: 0$' "$(vfg cmd 'data get entity Steve playerGameType' 2>&1)"
  expect "vfg state shows where he is" 'Steve 0 85 0 overworld' "$(vfg state 2>&1 | grep '^online')"
  players look Steve 90 -30 >/dev/null
  expect "look 90 -30, read back from Rotation" 'yaw 90.0 pitch -30.0$' "$(players pos Steve)"
  players look-at Steve 10.5 85 0.5 >/dev/null
  expect "look at a point east of him: yaw -90" 'yaw -90.0 pitch' "$(players pos Steve)"
  vfg cmd "scoreboard objectives add vfg_click minecraft.used:minecraft.carrot_on_a_stick" >/dev/null 2>&1
  players give Steve 'carrot_on_a_stick[custom_data={vfg_item:"smoke"},item_name="Smoke"]' >/dev/null
  expect "he holds it, custom_data and all" '^Test passed' \
    "$(vfg cmd 'execute if items entity Steve weapon.mainhand *[custom_data~{vfg_item:"smoke"}]' 2>&1)"
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
  expect "sneak: he is sneaking" '^Test passed' "$(vfg cmd "$sneaking" 2>&1)"
  players sneak Steve off >/dev/null
  sleep 0.3
  out=$(vfg cmd "$sneaking" 2>&1)
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
  expect "drop: the item lies by him" '^Test passed' "$(vfg cmd 'execute at Steve if entity @e[type=minecraft:item,distance=..6]' 2>&1)"
  expect "drop: his hand is empty" '^Test failed' "$(vfg cmd 'execute if items entity Steve weapon.mainhand *[custom_data~{vfg_item:"smoke"}]' 2>&1)"
  vfg cmd "kill @e[type=minecraft:item]" >/dev/null 2>&1
  players spawn Steve >/dev/null   # back on the arena's centre for what follows
else
  echo "== fake players: skipped (no Carpet; integration/realmc/realmc.py carpet, then run this again)"
fi

echo "== an action: railgun, run as a copy"
out=$(vfg action run railgun player=Steve 2>&1)
expect "vfg action run" '"ok": true, "copy": "railgun", "action": "railgun", "running": true' "$out"
railgun='execute if items entity Steve container.* *[custom_data~{vfg_item:"railgun"}]'
if [[ $mode == carpet ]]; then
  expect "it gave Steve the Railgun" '^Test passed' "$(vfg cmd "$railgun" 2>&1)"
fi
expect "vfg script list" '^railgun( |$)' "$(vfg script list --fresh 2>&1)"
logs=$(vfg script logs railgun 2>&1)
if [[ $logs == *Traceback* ]]; then bad "its log has no traceback" "$logs"; else ok "its log has no traceback"; fi
expect "vfg script stop (the stop action)" '^stopped railgun' "$(vfg script stop railgun 2>&1)"
if [[ $mode == carpet ]]; then
  expect "its cleanup took the Railgun back" '^Test failed' "$(vfg cmd "$railgun" 2>&1)"
fi
if [[ $mode == carpet ]]; then
  players kill Steve >/dev/null
  expect "kill: he has left" '^There are 0 of a max' "$(vfg cmd list 2>&1)"
fi

if (( keep )); then
  echo "== kept running: eval \"\$($here/env $name)\""
else
  echo "== down $name"
  "$here/down" "$name" && ok "down" || bad "down" "exit $?"
fi
if (( fails )); then echo "SMOKE FAIL: $fails check(s)"; exit 1; fi
echo "SMOKE PASS"
