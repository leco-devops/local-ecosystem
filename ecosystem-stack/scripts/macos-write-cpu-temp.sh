#!/usr/bin/env bash
# Write Mac CPU-related heat signal (°C) to a file the dashboard container reads
# (DASHBOARD_HOST_CPU_TEMP_FILE → ~/.local-eco-host-metrics/cpu_temp_c.txt).
#
# Order:
#   1) osx-cpu-temp (brew install osx-cpu-temp) — only if reading looks sane (not 0, in 15–125°C).
#   2) sudo powermetrics --samplers smc — Intel-style CPU die °C when SMC sampler works.
#   3) sudo powermetrics -s thermal — Apple Silicon: no die °C; maps thermal pressure to a baseline
#      proxy °C, then adds an offset from host load average (sysctl vm.loadavg ÷ hw.ncpu) so the
#      value moves under LLM/CPU load even when pressure stays "Nominal". Still not real die temp.
#
# Optional env:
#   LOCAL_ECO_THERMAL_PROXY_LOAD_SCALE  max °C to add at saturated load ratio (default 18)
#   LOCAL_ECO_CPU_TEMP_SILENCE_OSX_BOGUS=1  suppress stderr when osx-cpu-temp returns 0°C
#
# Scheduling: deploying the dashboard on macOS runs ecosystem-stack/scripts/macos-host-metrics-scheduler.sh
# install (LaunchAgent, default every 30s). Stopping/removing the dashboard uninstalls it.
# Passwordless sudo for /usr/bin/powermetrics helps for (2)(3) when unattended, e.g.:
#   youruser ALL=(root) NOPASSWD: /usr/bin/powermetrics

set -euo pipefail

# Cron / launchd use a tiny PATH — Homebrew is often missing otherwise.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

OUT="${1:-${HOME}/.local-eco-host-metrics/cpu_temp_c.txt}"
DIR="$(dirname "$OUT")"
mkdir -p "$DIR"

die_temp_plausible() {
  # osx-cpu-temp often returns 0.0°C on newer Apple Silicon / macOS where SMC path is empty.
  awk -v n="${1:?}" 'BEGIN { exit !(n > 0 && n >= 15 && n <= 125) }'
}

first_float() {
  printf '%s' "${1:-}" | grep -oE '[0-9]+(\.[0-9]+)?' | head -1
}

run_powermetrics() {
  # Non-interactive jobs: sudo -n only. Interactive terminal: allow a sudo prompt.
  if sudo -n true 2>/dev/null; then
    sudo -n powermetrics "$@" 2>/dev/null
  elif [ -t 0 ]; then
    sudo powermetrics "$@" 2>/dev/null
  else
    return 1
  fi
}

write_out_json() {
  local cpu_c="$1" source="$2" pressure="${3:-}"
  local tmp="${OUT}.tmp"
  if [ -n "$pressure" ]; then
    printf '{"cpu_temp_c":%s,"source":"%s","thermal_pressure":"%s"}\n' "$cpu_c" "$source" "$pressure" >"$tmp"
  else
    printf '{"cpu_temp_c":%s,"source":"%s"}\n' "$cpu_c" "$source" >"$tmp"
  fi
  mv "$tmp" "$OUT"
}

# Sidecar for LEco DevOps (read via /api/host-metrics/injected).
write_writer_status() {
  local success="$1" cpu="$2" src="$3" pressure="${4:-}" msg="${5:-}"
  export WTD_DIR="$DIR" WTD_SUCCESS="$success" WTD_CPU="$cpu" WTD_SRC="$src" WTD_PRESSURE="$pressure" WTD_MSG="$msg"
  python3 - <<'PY' 2>/dev/null || true
import json, os, datetime

DIR = os.environ["WTD_DIR"]
ok = os.environ["WTD_SUCCESS"] == "1"
cpu = os.environ.get("WTD_CPU") or ""
src = os.environ.get("WTD_SRC") or ""
pr = os.environ.get("WTD_PRESSURE") or ""
msg = os.environ.get("WTD_MSG") or ""

def fcpu():
    try:
        return float(cpu) if cpu.strip() else None
    except ValueError:
        return None

path = os.path.join(DIR, "writer_status.json")
obj = {
    "updated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "success": ok,
    "cpu_temp_c": fcpu(),
    "source": src or None,
    "thermal_pressure": pr or None,
    "message": (msg or None),
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fp:
    json.dump(obj, fp, indent=2)
os.replace(tmp, path)
PY
}

# --- 1) osx-cpu-temp ---
OSX_CPU_TEMP=""
for candidate in osx-cpu-temp /opt/homebrew/bin/osx-cpu-temp /usr/local/bin/osx-cpu-temp; do
  if command -v "$candidate" >/dev/null 2>&1; then
    OSX_CPU_TEMP="$candidate"
    break
  fi
  if [ -x "$candidate" ]; then
    OSX_CPU_TEMP="$candidate"
    break
  fi
done

if [ -n "$OSX_CPU_TEMP" ]; then
  raw="$("$OSX_CPU_TEMP" 2>/dev/null | tr -d '\n' || true)"
  num="$(first_float "$raw")"
  if [ -n "$num" ] && die_temp_plausible "$num"; then
    write_out_json "$num" "osx_cpu_temp"
    write_writer_status 1 "$num" "osx_cpu_temp" "" ""
    echo "Wrote ${num}°C (osx-cpu-temp) → $OUT"
    exit 0
  fi
  if [ -n "$num" ] && [ "${LOCAL_ECO_CPU_TEMP_SILENCE_OSX_BOGUS:-}" != "1" ]; then
    echo "osx-cpu-temp returned ${num}°C — ignored (expected bogus on some Apple Silicon); trying powermetrics…" >&2
  fi
fi

# --- 2) powermetrics SMC (Intel / older macOS) ---
if command -v powermetrics >/dev/null 2>&1; then
  smc_out="$(run_powermetrics --samplers smc -i 1 -n 1 || true)"
  if echo "$smc_out" | grep -q 'CPU die temperature'; then
    num="$(echo "$smc_out" | grep -E 'CPU die temperature:' | head -1 | sed -n 's/.*: \([0-9.]*\) C.*/\1/p')"
    if [ -n "$num" ] && die_temp_plausible "$num"; then
      write_out_json "$num" "smc_die"
      write_writer_status 1 "$num" "smc_die" "" ""
      echo "Wrote ${num}°C (powermetrics smc) → $OUT"
      exit 0
    fi
  fi

  # --- 3) powermetrics thermal (Apple Silicon — pressure only, proxy °C) ---
  th_out="$(run_powermetrics -s thermal -n 1 -i 1 || true)"
  pressure=""
  proxy=""
  case "$th_out" in
    *[Cc]ritical*) pressure="Critical"; proxy="88" ;;
    *[Ss]erious*) pressure="Serious"; proxy="72" ;;
    *[Ff]air*) pressure="Fair"; proxy="58" ;;
    *[Nn]ominal*) pressure="Nominal"; proxy="42" ;;
  esac
  if [ -n "$proxy" ] && [ -n "$pressure" ]; then
    # Baseline °C per pressure bucket; add loadavg-based °C so "Nominal" moves under heavy CPU (still not die temp).
    cores="$(sysctl -n hw.ncpu 2>/dev/null | head -1 | tr -cd '0-9')"
    [ -z "$cores" ] && cores=8
    [ "$cores" -lt 1 ] && cores=8
    load1="$(sysctl -n vm.loadavg 2>/dev/null | awk '{gsub(/[{}]/,""); print $2}')"
    [ -z "$load1" ] && load1=0
    scale="${LOCAL_ECO_THERMAL_PROXY_LOAD_SCALE:-18}"
    read -r ratio bonus final_c <<EOF
$(awk -v L="$load1" -v C="$cores" -v S="$scale" -v B="$proxy" 'BEGIN {
  if (C < 1) C = 1
  r = L / C
  if (r < 0) r = 0
  if (r > 1.35) r = 1.35
  b = r * S
  f = B + b
  if (f > 96) f = 96
  if (f < 32) f = 32
  printf "%.4f %.2f %.1f\n", r, b, f
}')
EOF
    export WTOUT="$OUT" WTBASE="$proxy" WTPRESS="$pressure" WTRAT="$ratio" WTADD="$bonus" WTFINAL="$final_c"
    if python3 - <<'PY'
import json, os

out = os.environ["WTOUT"]
path = out + ".tmp"
obj = {
    "cpu_temp_c": float(os.environ["WTFINAL"]),
    "source": "thermal_pressure",
    "thermal_pressure": os.environ["WTPRESS"],
    "proxy_baseline_c": float(os.environ["WTBASE"]),
    "proxy_load_ratio": float(os.environ["WTRAT"]),
    "proxy_load_add_c": float(os.environ["WTADD"]),
    "proxy_model": "pressure_plus_loadavg",
}
with open(path, "w", encoding="utf-8") as fp:
    json.dump(obj, fp, indent=2)
os.replace(path, out)
PY
    then
      true
    else
      write_out_json "$final_c" "thermal_pressure" "$pressure"
    fi
    write_writer_status 1 "$final_c" "thermal_pressure" "$pressure" "load1=${load1} ncpu=${cores} +${bonus}°C"
    echo "Wrote proxy ${final_c}°C (thermal: $pressure, baseline ${proxy}°C + load ${bonus}°C, load1=${load1} ncpu=${cores}) → $OUT"
    exit 0
  fi
fi

echo "No usable temperature: osx-cpu-temp missing/bogus and powermetrics unavailable or needs sudo (see script header)." >&2
write_writer_status 0 "" "" "" "no usable temperature (osx-cpu-temp / powermetrics)"
exit 1
