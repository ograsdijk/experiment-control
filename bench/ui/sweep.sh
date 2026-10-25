#!/usr/bin/env bash
# Full UI benchmark sweep (~2 h). Runs each configuration with its own
# stack/gateway launch, sequentially, so runs never overlap.
#   bash bench/ui/sweep.sh [out_dir]
set -u
cd "$(dirname "$0")/../.."
OUT="${1:-bench/ui/results/sweep-$(date +%Y%m%d-%H%M)}"
mkdir -p "$OUT"
RUN=(uv run --with psutil python -m bench.ui.run --launch --out "$OUT")

run() {
  echo "=== $(date +%H:%M:%S) $*" | tee -a "$OUT/sweep.log"
  "${RUN[@]}" "$@" >>"$OUT/sweep.log" 2>&1 || echo "!!! FAILED: $*" | tee -a "$OUT/sweep.log"
}

# Re-baseline against PERFORMANCE.md checkpoints
run --scenario steady --preset baseline --profile telemetry_heavy --repeats 3
run --scenario steady --preset heavy --profile mixed_heavy --device-panel collapsed --repeats 3

# Steady state at the limits / realistic layout
run --scenario steady --preset heavy --profile mixed_heavy --repeats 3
run --scenario steady --preset stress --profile mixed_heavy --device-panel collapsed --repeats 3
run --scenario steady --preset stress --profile mixed_heavy --repeats 3
run --scenario steady --preset heavy --profile many_panels --repeats 3 --width 2560 --height 1440 --tag 1440p
run --scenario steady --preset heavy --profile mixed_heavy --repeats 1 --network --tag network
run --scenario steady --preset heavy --profile mixed_heavy --repeats 1 --trace --duration 30 --tag trace

# Load
run --scenario load --preset baseline --profile baseline --cache cold --repeats 5 --duration 8
run --scenario load --preset heavy --profile mixed_heavy --cache cold --repeats 5 --duration 8
run --scenario load --preset heavy --profile mixed_heavy --cache warm --repeats 5 --duration 8

# Interactions under load
run --scenario interactions --preset heavy --profile mixed_heavy --repeats 3 --warmup 30

# Concurrent clients (gateway fan-out)
run --scenario clients --preset heavy --profile mixed_heavy --clients 1,2,4,8 --warmup 30 --duration 30 --repeats 1

# Slow-PC emulation
run --scenario steady --preset heavy --profile mixed_heavy --cpu-throttle 4 --repeats 3
run --scenario steady --preset heavy --profile mixed_heavy --device-panel collapsed --cpu-throttle 4 --repeats 3
run --scenario interactions --preset heavy --profile mixed_heavy --cpu-throttle 4 --repeats 2 --warmup 30
run --scenario load --preset heavy --profile mixed_heavy --cache cold --cpu-throttle 4 --repeats 3 --duration 8

# Memory soak with churn (30 min steady after churn)
run --scenario soak --preset heavy --profile mixed_heavy --repeats 1 --duration 1800 --sample-every 120 \
  --modal-cycles 10 --profile-cycles many_panels,mixed_heavy,many_panels,mixed_heavy,many_panels,mixed_heavy

echo "=== $(date +%H:%M:%S) sweep done" | tee -a "$OUT/sweep.log"
