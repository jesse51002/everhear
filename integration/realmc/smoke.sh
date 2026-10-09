#!/usr/bin/env bash
# The realmc smoke test, against a real 26.3 server: up (its world fresh from the template), one
# command through vfg, a fill checked with `execute if blocks`, a fake player (with Carpet) that
# turns, right-clicks a carrot_on_a_stick and sneaks, one seed script run for a few seconds and
# stopped, down. PASS or FAIL per check; exit 1 if any failed.
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
expect "time query daytime" '^The time is [0-9]+$' "$(vfg cmd 'time query daytime' 2>&1)"
expect "list" '^There are [0-9]+ of a max of [0-9]+ players online' "$(vfg cmd list 2>&1)"

echo "== fill, then execute if blocks"
x=$((ax + 4)) y=$ay z=$((az + 4))
expect "fill 3x3x3 gold" '^Successfully filled 27 block' "$(vfg cmd "fill $x $y $z $((x+2)) $((y+2)) $((z+2)) minecraft:gold_block" 2>&1)"
expect "fill its twin" '^Successfully filled 27 block' "$(vfg cmd "fill $((x+4)) $y $z $((x+6)) $((y+2)) $((z+2)) minecraft:gold_block" 2>&1)"
expect "the two match" '^Test passed, count: 27$' "$(vfg cmd "execute if blocks $x $y $z $((x+2)) $((y+2)) $((z+2)) $((x+4)) $y $z all" 2>&1)"
vfg cmd "setblock $((x+5)) $((y+1)) $((z+1)) minecraft:stone" >/dev/null 2>&1
expect "one block changed: they differ" '^Test failed' "$(vfg cmd "execute if blocks $x $y $z $((x+2)) $((y+2)) $((z+2)) $((x+4)) $y $z all" 2>&1)"
expect "that block is stone" '^Test passed$' "$(vfg cmd "execute if block $((x+5)) $((y+1)) $((z+1)) minecraft:stone" 2>&1)"
vfg cmd "fill $x $y $z $((x+6)) $((y+2)) $((z+2)) minecraft:air" >/dev/null 2>&1

players() { python3 "$here/players.py" --name "$name" "$@" 2>&1; }
if [[ $mode == carpet ]]; then
  echo "== a fake player (Carpet)"
  expect "spawn Steve on the arena" "^Steve at " "$(players spawn Steve)"
  players look Steve 90 -30 >/dev/null
  expect "look 90 -30" 'yaw 90.0 pitch -30.0$' "$(players pos Steve)"
  vfg cmd "scoreboard objectives add rmc_used minecraft.used:minecraft.carrot_on_a_stick" >/dev/null 2>&1
  players give Steve "carrot_on_a_stick[custom_name='Smoke']" >/dev/null
  players use Steve >/dev/null
  sleep 0.3
  expect "right-click counts in used:carrot_on_a_stick" '^Steve has 1 \[rmc_used\]$' \
    "$(vfg cmd 'scoreboard players get Steve rmc_used' 2>&1)"
  players sneak Steve on >/dev/null
  sleep 0.3
  expect "it is sneaking" '^Test passed' \
    "$(vfg cmd 'execute as Steve if predicate {condition:"minecraft:entity_properties",entity:"this",predicate:{flags:{is_sneaking:true}}}' 2>&1)"
  players sneak Steve off >/dev/null
  vfg cmd "scoreboard objectives remove rmc_used" >/dev/null 2>&1
else
  echo "== fake players: skipped (no Carpet; integration/realmc/realmc.py carpet, then run this again)"
fi

echo "== a seed script: tnt_rain"
out=$(vfg script run tnt_rain 2>&1)
expect "vfg script run" '^running tnt_rain pid [0-9]+' "$out"
sleep 1.5  # its first volley comes at once; the TNT's fuse is 4 s
if [[ $mode == carpet ]]; then
  expect "it dropped TNT on Steve" '^Test passed' "$(vfg cmd 'execute if entity @e[type=minecraft:tnt]' 2>&1)"
fi
expect "vfg script list" 'tnt_rain running pid [0-9]+' "$(vfg script list 2>&1)"
logs=$(vfg script logs tnt_rain 2>&1)
if [[ $logs == *Traceback* ]]; then bad "its log has no traceback" "$logs"; else ok "its log has no traceback"; fi
expect "vfg script stop" '^stopped tnt_rain' "$(vfg script stop tnt_rain 2>&1)"
expect "its cleanup took its TNT away" '^Test failed' "$(vfg cmd 'execute if entity @e[type=minecraft:tnt,tag=vfg_tnt_rain]' 2>&1)"
[[ $mode == carpet ]] && players kill Steve >/dev/null

if (( keep )); then
  echo "== kept running: eval \"\$($here/env $name)\""
else
  echo "== down $name"
  "$here/down" "$name" && ok "down" || bad "down" "exit $?"
fi
if (( fails )); then echo "SMOKE FAIL: $fails check(s)"; exit 1; fi
echo "SMOKE PASS"
