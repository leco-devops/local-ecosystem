#!/bin/bash
set -euo pipefail

BASE_URL="http://127.0.0.1"

echo "Seeding R2 bucket..."
curl -sS -X POST "$BASE_URL/buckets" -H "Host: r2.lh" -H "Content-Type: application/json" -d '{"name":"dev-bucket"}' >/dev/null
curl -sS -X PUT "$BASE_URL/objects/dev-bucket/hello.txt" -H "Host: r2.lh" --data-binary "hello from local r2" >/dev/null

echo "Seeding KV namespace..."
curl -sS -X POST "$BASE_URL/namespaces" -H "Host: kv.lh" -H "Content-Type: application/json" -d '{"name":"dev-kv"}' >/dev/null
curl -sS -X PUT "$BASE_URL/namespaces/dev-kv/values/sample" -H "Host: kv.lh" --data-binary "sample-value" >/dev/null

echo "Seeding D1 database..."
curl -sS -X POST "$BASE_URL/databases" -H "Host: d1.lh" -H "Content-Type: application/json" -d '{"name":"devdb"}' >/dev/null
curl -sS -X POST "$BASE_URL/databases/devdb/execute" -H "Host: d1.lh" -H "Content-Type: application/json" -d '{"sql":"CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, name TEXT);"}' >/dev/null
curl -sS -X POST "$BASE_URL/databases/devdb/execute" -H "Host: d1.lh" -H "Content-Type: application/json" -d '{"sql":"INSERT INTO items(name) VALUES (?);","params":["seed-item"]}' >/dev/null

echo "Seed complete."
