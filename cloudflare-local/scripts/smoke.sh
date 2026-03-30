#!/bin/bash
set -euo pipefail

BASE_URL="http://127.0.0.1"

echo "Smoke: R2"
curl -fsS "$BASE_URL/health" -H "Host: r2.lh" >/dev/null
curl -fsS -X POST "$BASE_URL/buckets" -H "Host: r2.lh" -H "Content-Type: application/json" -d '{"name":"smoke-bucket"}' >/dev/null
curl -fsS -X PUT "$BASE_URL/objects/smoke-bucket/smoke.txt" -H "Host: r2.lh" --data-binary "ok" >/dev/null
curl -fsS "$BASE_URL/objects/smoke-bucket/smoke.txt" -H "Host: r2.lh" >/dev/null

echo "Smoke: KV"
curl -fsS "$BASE_URL/health" -H "Host: kv.lh" >/dev/null
curl -fsS -X POST "$BASE_URL/namespaces" -H "Host: kv.lh" -H "Content-Type: application/json" -d '{"name":"smoke-kv"}' >/dev/null
curl -fsS -X PUT "$BASE_URL/namespaces/smoke-kv/values/a?ttl=15" -H "Host: kv.lh" --data-binary "1" >/dev/null
curl -fsS "$BASE_URL/namespaces/smoke-kv/values/a" -H "Host: kv.lh" >/dev/null

echo "Smoke: D1"
curl -fsS "$BASE_URL/health" -H "Host: d1.lh" >/dev/null
curl -fsS -X POST "$BASE_URL/databases" -H "Host: d1.lh" -H "Content-Type: application/json" -d '{"name":"smoke-db"}' >/dev/null
curl -fsS -X POST "$BASE_URL/databases/smoke-db/execute" -H "Host: d1.lh" -H "Content-Type: application/json" -d '{"sql":"CREATE TABLE IF NOT EXISTS t(v TEXT);"}' >/dev/null
curl -fsS -X POST "$BASE_URL/databases/smoke-db/query" -H "Host: d1.lh" -H "Content-Type: application/json" -d '{"sql":"SELECT name FROM sqlite_master LIMIT 1;"}' >/dev/null

echo "Smoke: Workers"
curl -fsS "$BASE_URL/health" -H "Host: workers.lh" >/dev/null

echo "Smoke: Autoscaler"
curl -fsS "$BASE_URL/status" -H "Host: autoscale.lh" >/dev/null

echo "All smoke checks passed."
