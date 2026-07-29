#!/usr/bin/env bash
# v8 import → preview → commit → home → context-preview smoke (branch feature only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${ROOT}/deploy/.env"
BASE_URL="${BASE_URL:-http://127.0.0.1}"
TOKEN="${ADMIN_API_TOKEN:-}"
if [[ -z "$TOKEN" && -f "$ENV_FILE" ]]; then
  TOKEN="$(grep '^ADMIN_API_TOKEN=' "$ENV_FILE" | cut -d= -f2-)"
fi
if [[ -z "$TOKEN" ]]; then
  echo "ADMIN_API_TOKEN required" >&2
  exit 2
fi
AUTH=(-H "Authorization: Bearer $TOKEN")
SAMPLE="${1:-}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [[ -z "$SAMPLE" ]]; then
  SAMPLE="$TMP/smoke_proposal.md"
  cat >"$SAMPLE" <<'EOF'
# 《烟渚短测》企划
## 一句话卖点
雾港养女，灯下慈父。
## 类型与体量
玄幻。目标 12 章。
## 世界观
- 烟渚港终年薄雾。
- 规则：不可直视雾心灯，违者失忆。
- 地点：烟渚港、旧灯塔、慈父宅
## 人物
### 顾衡
主角，慈父。
### 小棠
养女。
## 卷纲
第一卷 雾起（第1-6章）
第二卷 灯灭（第7-12章）
## 章纲
第1章 入港
第2章 规矩
第3章 灯影
## 写作要求
禁止系统面板。
EOF
fi

echo "== health =="
curl -sS -o /tmp/v8_ready.json -w "%{http_code}\n" "$BASE_URL/health/ready" | tee /tmp/v8_ready.code
test "$(cat /tmp/v8_ready.code)" = "200"

echo "== upload =="
UP=$(curl -sS "${AUTH[@]}" -F "file=@${SAMPLE};filename=$(basename "$SAMPLE")" \
  "$BASE_URL/api/import-sessions")
echo "$UP" | python3 -m json.tool
SID=$(echo "$UP" | python3 -c 'import sys,json;print(json.load(sys.stdin)["import_session_id"])')
echo "SID=$SID"

echo "== poll analysis =="
for i in $(seq 1 120); do
  S=$(curl -sS "${AUTH[@]}" "$BASE_URL/api/import-sessions/$SID")
  st=$(echo "$S" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("status"), d.get("current_step"), d.get("progress"))')
  echo "poll$i $st"
  echo "$S" | python3 -c 'import sys,json;d=json.load(sys.stdin);import sys as s; s.exit(0 if d.get("status") in ("preview_ready","needs_human","failed","completed") else 1)' && break
  sleep 6
done

PREV=$(curl -sS "${AUTH[@]}" "$BASE_URL/api/import-sessions/$SID/preview")
echo "$PREV" | python3 -c 'import sys,json;d=json.load(sys.stdin);p=d.get("preview") or {};print("status",d.get("status"));print("step",d.get("current_step"));print("err",d.get("error_detail"));print("title",p.get("title_guess"));print("logline",p.get("logline") or (p.get("metadata") or {}).get("logline"));print("genre",p.get("genre") or (p.get("metadata") or {}).get("genre"));print("counts",p.get("counts"));print("open",[c.get("code") for c in d.get("conflicts") or [] if c.get("status")=="open"])'
STAT=$(echo "$PREV" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("status"))')
if [[ "$STAT" != "preview_ready" && "$STAT" != "needs_human" ]]; then
  echo "analysis did not reach preview_ready: $STAT" >&2
  curl -sS "${AUTH[@]}" "$BASE_URL/api/import-sessions/$SID" | python3 -m json.tool | head -80 >&2 || true
  exit 1
fi
HASH=$(echo "$PREV" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("preview_hash") or "")')
TITLE=$(echo "$PREV" | python3 -c 'import sys,json;d=json.load(sys.stdin);p=d.get("preview") or {};print(p.get("title_guess") or "smoke")')

echo "== resolve-batch warnings =="
curl -sS "${AUTH[@]}" -H 'Content-Type: application/json' -d '{"mode":"warnings"}' \
  "$BASE_URL/api/import-sessions/$SID/conflicts/resolve-batch" | python3 -m json.tool

echo "== commit =="
COMMIT=$(curl -sS "${AUTH[@]}" -H 'Content-Type: application/json' \
  -d "{\"expected_preview_hash\":\"$HASH\",\"book_overrides\":{\"title\":\"${TITLE}-smoke\"},\"auto_resolve_warnings\":true}" \
  "$BASE_URL/api/import-sessions/$SID/commit")
echo "$COMMIT" | python3 -m json.tool
BID=$(echo "$COMMIT" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("book_id") or "")')
test -n "$BID"

echo "== home =="
HOME=$(curl -sS "${AUTH[@]}" "$BASE_URL/api/library/books/$BID/home")
echo "$HOME" | python3 -c 'import sys,json;d=json.load(sys.stdin);b=d.get("book") or {};print("title",b.get("title"));print("logline",b.get("logline"));print("genre",b.get("genre"));print("tags",b.get("tags"));print("counts",d.get("counts"));print("chars",[c.get("name") for c in ((d.get("entities") or {}).get("characters") or [])]);print("locs",[c.get("name") for c in ((d.get("entities") or {}).get("locations") or [])])'

echo "== context-preview =="
curl -sS "${AUTH[@]}" "$BASE_URL/api/library/books/$BID/context-preview?chapter_no=1" \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("ok",d.get("ok"),"ver",d.get("assembler_version"),"items",d.get("item_count"));print("kinds",d.get("kinds"))'

echo "SMOKE_OK book_id=$BID session_id=$SID"
