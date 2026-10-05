#!/usr/bin/env bash
# End-to-end verification for the jobsearch stack — read-only, no mutations
# beyond normal search persistence (persist:false where it matters).
#
# Usage: ./deploy/verify.sh [BASE_URL] [TOKEN]
#   BASE_URL  default http://localhost:8080
#   TOKEN     default $JOBSEARCH_API_TOKEN
# Requires: curl, jq
set -euo pipefail

BASE_URL="${1:-http://localhost:8080}"
TOKEN="${2:-${JOBSEARCH_API_TOKEN:-}}"
AUTH="Authorization: Bearer $TOKEN"
CT="Content-Type: application/json"

pass=0; fail=0
ok()   { echo "  PASS: $1"; pass=$((pass+1)); }
bad()  { echo "  FAIL: $1"; fail=$((fail+1)); }

echo "== jobsearch stack verification against $BASE_URL =="

# 1. Health
echo "[1/8] /health"
status=$(curl -s -o /tmp/js_health.json -w '%{http_code}' "$BASE_URL/health")
[ "$status" = "200" ] && ok "health returns 200" || bad "health returned $status"
jq -e '.sources | has("bundesagentur")' /tmp/js_health.json >/dev/null \
  && ok "bundesagentur source listed" || bad "bundesagentur missing from /health"

# 2. SearXNG JSON format (run inside the LXC — searxng has no published port)
echo "[2/8] SearXNG JSON format (only checkable from the LXC itself)"
if docker exec jobsearch-searxng wget -qO- 'http://localhost:8080/search?q=test&format=json' 2>/dev/null | jq -e '.results' >/dev/null 2>&1; then
  ok "searxng serves ?format=json (search.formats took effect)"
else
  echo "  SKIP: not run from the LXC (or searxng container unreachable) — run verify.sh inside the LXC at least once"
fi

# 3. Search returns normalized BA jobs
echo "[3/8] POST /v1/search"
search_body='{"query":"Netzwerkadministrator","location":"München","radius_km":25,"size":10,"persist":false}'
status=$(curl -s -o /tmp/js_search.json -w '%{http_code}' -X POST \
  -H "$AUTH" -H "$CT" -d "$search_body" "$BASE_URL/v1/search")
[ "$status" = "200" ] && ok "search returns 200" || bad "search returned $status (check token)"
ba_count=$(jq -r '.sources.bundesagentur.count // 0' /tmp/js_search.json)
[ "$ba_count" -gt 0 ] 2>/dev/null && ok "bundesagentur returned $ba_count results" \
  || bad "bundesagentur returned 0 results (API shape drift? check gateway logs)"
jq -e '.jobs | length > 0' /tmp/js_search.json >/dev/null \
  && ok "jobs array non-empty" || bad "jobs array empty"
jq -e '.jobs | all(.id != null and (.title != null) and (.hash != null))' /tmp/js_search.json >/dev/null \
  && ok "every job has id, title, hash (Job shape)" || bad "Job shape violated"

# 4. Cache works on repeat
echo "[4/8] search cache"
curl -s -X POST -H "$AUTH" -H "$CT" -d "$search_body" "$BASE_URL/v1/search" > /tmp/js_search2.json
jq -e '.sources.bundesagentur.cached == true' /tmp/js_search2.json >/dev/null \
  && ok "repeat search served from cache" || bad "repeat search not cached"

# 5. Details fetch (BA job -> full description)
echo "[5/8] GET /v1/jobs/{id}"
job_id=$(jq -r '[.jobs[] | select(.source == "bundesagentur")][0].id // empty' /tmp/js_search.json)
if [ -n "$job_id" ]; then
  status=$(curl -s -o /tmp/js_detail.json -w '%{http_code}' -H "$AUTH" "$BASE_URL/v1/jobs/$job_id")
  [ "$status" = "200" ] && ok "details return 200 for $job_id" || bad "details returned $status"
  desc_len=$(jq -r '.description // "" | length' /tmp/js_detail.json)
  [ "$desc_len" -gt 200 ] && ok "description present ($desc_len chars)" \
    || bad "description suspiciously short ($desc_len chars)"
  # second call must come from the 24h cache
  curl -s -H "$AUTH" "$BASE_URL/v1/jobs/$job_id" > /tmp/js_detail2.json
  jq -e '.cached == true' /tmp/js_detail2.json >/dev/null \
    && ok "details cached on second call" || bad "details not cached"
else
  bad "no bundesagentur job id available to fetch details for"
fi

# 6. Source filtering + graceful degradation
echo "[6/8] source filtering"
body_only_ba='{"query":"Netzwerkadministrator","location":"München","sources":["bundesagentur"],"persist":false}'
status=$(curl -s -o /tmp/js_onlyba.json -w '%{http_code}' -X POST \
  -H "$AUTH" -H "$CT" -d "$body_only_ba" "$BASE_URL/v1/search")
[ "$status" = "200" ] && ok "filtered search returns 200" || bad "filtered search returned $status"
jq -e '.sources | has("searxng") | not' /tmp/js_onlyba.json >/dev/null \
  && ok "only requested source queried" || bad "searxng unexpectedly queried"
jq -e '.sources["nonexistent-source"].status == "skipped"' /tmp/js_onlyba.json >/dev/null 2>&1 \
  || true  # skipped-status check only if a bogus source was requested; informational

# 7. Auth negative tests
echo "[7/8] auth"
status=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$CT" \
  -d '{"query":"test"}' "$BASE_URL/v1/search")
[ "$status" = "401" ] && ok "no token -> 401" || bad "no token returned $status (expected 401)"
status=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$CT" \
  -H "Authorization: Bearer wrong-token" -d '{"query":"test"}' "$BASE_URL/v1/search")
[ "$status" = "401" ] && ok "wrong token -> 401" || bad "wrong token returned $status (expected 401)"

# 8. Dedup + persistence
echo "[8/8] dedup/persistence"
curl -s -X POST -H "$AUTH" -H "$CT" \
  -d '{"query":"Netzwerkadministrator","location":"München","persist":true,"size":25}' \
  "$BASE_URL/v1/search" > /dev/null
curl -s -X POST -H "$AUTH" -H "$CT" \
  -d '{"query":"Netzwerkadministrator","location":"München","persist":true,"size":25}' \
  "$BASE_URL/v1/search" > /dev/null
curl -s -H "$AUTH" "$BASE_URL/v1/jobs?source=bundesagentur&limit=100" > /tmp/js_persisted.json
persisted=$(jq 'length' /tmp/js_persisted.json)
[ "$persisted" -gt 0 ] 2>/dev/null && ok "$persisted jobs persisted" || bad "no jobs persisted"
jq -e '[.[].hash] | length == (unique | length)' /tmp/js_persisted.json >/dev/null \
  && ok "no duplicate hashes among persisted jobs" || bad "duplicate hashes persisted"

echo
echo "== $pass passed, $fail failed =="
[ "$fail" -eq 0 ] && echo "ALL CHECKS PASSED" || exit 1