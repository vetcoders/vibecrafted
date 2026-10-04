#!/bin/zsh
# Runtime acceptance canon: generation identity probe + live PTY scenes.
#
# Modes:
#   (default)      scenes + one identity snapshot of the installed runtime
#   --upgrade      full phased flow: BEFORE -> [install] -> AFTER ->
#                  app launch (1s sampling x30) -> [Terminal click] ->
#                  scenes on the new runtime
#   --frame-bin P  run scenes against an explicit vc-frame binary
#   --strict-organs  S5 organ-guard counts as FAIL (default: WARN)
#
# Canon rules (see README.md): populated world for navigation scenes,
# isolated socket dir, honest exit status, no `| tail` over gates.
set -u
HERE="${0:A:h}"
MODE="scenes"; FRAME_BIN=""; STRICT=""
for arg in "$@"; do case "$arg" in
  --upgrade) MODE="upgrade";;
  --strict-organs) STRICT="--strict-organs";;
  --frame-bin=*) FRAME_BIN="${arg#*=}";;
esac; done

BINS=(vibecrafted vc-frame vc-start vc-server vc-terminal vc-o vc-guardian vc-server-supervisor vibecrafted-mcp)
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${VC_ACCEPT_OUT:-$HOME/vc-accept-$STAMP}"; mkdir -p "$OUT"
TIMELINE="$OUT/proc-timeline.log"

snap_procs() {
  ps -axo pid,ppid,lstart,command 2>/dev/null \
    | grep -E 'share/vibecrafted|Vibecrafted\.app' | grep -v grep \
    | sed "s/^/$(date +%H:%M:%S) [$1] /" >> "$TIMELINE"
  echo "$(date +%H:%M:%S) [$1] ---sample-end---" >> "$TIMELINE"
}
SAMPLER_PID=""
start_sampler() { ( while :; do snap_procs bg; sleep 2; done ) & SAMPLER_PID=$!; }
stop_sampler()  { [[ -n "$SAMPLER_PID" ]] && kill "$SAMPLER_PID" 2>/dev/null; SAMPLER_PID=""; }
trap 'stop_sampler' EXIT INT TERM

probe() {
  local phase="$1" f="$OUT/${1// /_}.log"
  snap_procs "phase:$phase"
  echo "==== probe — $phase · $(date +%H:%M:%S) ====" | tee "$f"
  for b in "${BINS[@]}"; do
    { echo; echo "######## $b ########"
      if command -v "$b" >/dev/null; then
        command -v diagnose-which >/dev/null && diagnose-which "$b" 2>&1 \
          || { echo "(diagnose-which niedostępny)"; $b --version 2>&1 | head -1; }
        command -v bin-provenance >/dev/null \
          && { echo "-------- provenance --------"; bin-provenance "$b" 2>&1; }
      else echo "NIEOBECNE w PATH"; fi
    } >> "$f"
  done
  { echo; echo "-------- launchd --------"
    launchctl list 2>/dev/null | grep -iE 'vetcoders|vibecrafted' || echo "(brak)"
  } >> "$f"
  echo "zapisane: $f"
}
pause() { echo; read -r "?>>> $1 — Enter, gdy gotowe <<<"; echo; }

scenes() {
  echo "==== sceny PTY ($(date +%H:%M:%S)) ===="
  local extra=()
  [[ -n "$FRAME_BIN" ]] && extra+=(--frame-bin "$FRAME_BIN")
  [[ -n "$STRICT" ]] && extra+=($STRICT)
  python3 "$HERE/frame_scenes.py" "${extra[@]}" | tee "$OUT/scenes.log"
  return ${pipestatus[1]}
}

RC=0
if [[ "$MODE" == "upgrade" ]]; then
  start_sampler
  probe "BEFORE install"
  pause "ZAINSTALUJ TERAZ (DMG → Applications wg instrukcji); sampler nagrywa"
  probe "AFTER install"
  echo "==== URUCHAMIAM APLIKACJĘ ===="
  open -a Vibecrafted || echo "uruchom ręcznie"
  for i in {1..30}; do snap_procs "launch+${i}s"; sleep 1; done
  probe "AFTER app launch"
  pause "KLIKNIJ 'Open Terminal' W APLIKACJI"
  for i in {1..10}; do snap_procs "terminal+${i}s"; sleep 1; done
  probe "AFTER Terminal click"
  stop_sampler
  scenes || RC=1
else
  start_sampler
  probe "SNAPSHOT"
  scenes || RC=1
  stop_sampler
fi

SUMMARY="$OUT/SUMMARY.txt"
{
  echo "== TL;DR tożsamości per faza =="
  for f in "$OUT"/*.log; do
    [[ "$f" == "$TIMELINE" || "$f" == "$OUT/scenes.log" ]] && continue
    echo; echo "### ${f:t:r}"
    awk '/^######## /{bin=$2} /NIEOBECNE w PATH/{printf "  %-20s BRAK\n",bin}
         /^Result:/{printf "  %-20s %s\n",bin,$0} /^Version:/{printf "  %-20s %s\n",bin,$0}
         /^Release container:/{printf "  %-20s %s\n",bin,$0}
         /diagnose-which niedostępny/{getline v; printf "  %-20s (fallback) %s\n",bin,v}' "$f"
  done
  echo; echo "== Sceny =="
  grep -E '^\[(PASS|FAIL|WARN)\]|^== scenes' "$OUT/scenes.log" 2>/dev/null || echo "(sceny nie pobiegły)"
  echo; echo "== Oś czasu generacji =="
  python3 - "$TIMELINE" <<'PY'
import sys,re,collections
counts=collections.Counter(); out=[]
for line in open(sys.argv[1],errors="replace"):
    m=re.match(r'(\S+) \[([^\]]+)\] (.*)',line.rstrip())
    if not m: continue
    t,tag,rest=m.groups()
    if rest=='---sample-end---':
        gens=' '.join(f'{g}={n}' for g,n in sorted(counts.items())) or 'brak'
        out.append((t,tag,gens)); counts=collections.Counter(); continue
    g=re.search(r'releases/([0-9.]+\+g[0-9a-f]+)',rest)
    counts[g.group(1) if g else ('app' if 'Vibecrafted.app' in rest else '?')]+=1
prev=None
for t,tag,gens in out:
    if gens!=prev or not tag.startswith('bg'):
        print(f"  {t} [{tag:>14}] {gens}")
    prev=gens
PY
} | tee "$SUMMARY"
echo; echo "Wyniki: $OUT · exit=$RC"
exit $RC
