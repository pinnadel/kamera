#!/usr/bin/env bash
#
# Dump the entire Fitiv support site (a Zendesk Help Center) to local files.
#
# Strategy:
#   1. Primary  -> Zendesk Help Center public API (full structured JSON, all
#                  articles/sections/categories, paginated automatically).
#   2. Fallback -> Wayback Machine (CDX index + archived snapshots) if the
#                  live API is blocked or returns nothing.
#
# Requirements: bash, curl, jq.  (pandoc optional, for HTML -> Markdown.)
#
# Usage:
#   ./fetch_fitiv_support.sh              # primary (Zendesk API)
#   ./fetch_fitiv_support.sh --wayback    # force the Wayback Machine route
#
set -euo pipefail

HOST="support.fitiv.com"
LOCALE="en-us"
OUT="fitiv_support_dump"
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
need curl; need jq

mkdir -p "$OUT/articles" "$OUT/raw"

# Convert an article's HTML body to Markdown if pandoc exists, else keep HTML.
write_article() {
  local id="$1" title="$2" body="$3" url="$4"
  local slug
  slug=$(echo "$title" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g')
  slug=${slug:-untitled}
  if command -v pandoc >/dev/null 2>&1; then
    { echo "# $title"; echo; echo "<!-- source: $url -->"; echo;
      printf '%s' "$body" | pandoc -f html -t gfm 2>/dev/null; } \
      > "$OUT/articles/${id}-${slug}.md"
  else
    { echo "<!-- title: $title -->"; echo "<!-- source: $url -->";
      printf '%s' "$body"; } > "$OUT/articles/${id}-${slug}.html"
  fi
}

fetch_zendesk() {
  echo ">> Trying Zendesk Help Center API on $HOST ..."
  # Grab the supporting taxonomy too (handy for organizing the dump).
  for kind in categories sections; do
    curl -fsS -A "$UA" \
      "https://$HOST/api/v2/help_center/$LOCALE/$kind.json?per_page=100" \
      -o "$OUT/raw/$kind.json" 2>/dev/null || true
  done

  local url="https://$HOST/api/v2/help_center/$LOCALE/articles.json?per_page=100"
  local page=1 total=0
  : > "$OUT/raw/articles.ndjson"

  while [ -n "$url" ] && [ "$url" != "null" ]; do
    echo "   page $page ..."
    local resp
    resp=$(curl -fsS -A "$UA" "$url") || { echo "   request failed"; return 1; }

    # Stream each article: id, title, body, html_url.
    echo "$resp" | jq -c '.articles[]' >> "$OUT/raw/articles.ndjson"
    while IFS=$'\t' read -r id title url2 body; do
      [ -z "$id" ] && continue
      write_article "$id" "$title" "$body" "$url2"
      total=$((total+1))
    done < <(echo "$resp" | jq -r '.articles[] | [.id, .title, .html_url, .body] | @tsv')

    url=$(echo "$resp" | jq -r '.next_page // empty')
    page=$((page+1))
  done

  if [ "$total" -eq 0 ]; then echo "   no articles returned"; return 1; fi
  echo ">> Done. Saved $total articles to $OUT/articles/  (raw JSON in $OUT/raw/)"
}

fetch_wayback() {
  echo ">> Falling back to the Wayback Machine for $HOST ..."
  local cdx="$OUT/raw/wayback_index.json"
  # List every archived URL under the host (latest snapshot per page).
  curl -fsS "http://web.archive.org/cdx/search/cdx?url=${HOST}/*&output=json&fl=timestamp,original&collapse=urlkey&filter=statuscode:200" \
    -o "$cdx"

  local n
  n=$(jq 'length - 1' "$cdx")
  echo "   found $n archived URLs; downloading snapshots ..."
  # Skip the header row (index 0).
  jq -r '.[1:][] | "\(.[0])\t\(.[1])"' "$cdx" | while IFS=$'\t' read -r ts orig; do
    # The "id_" modifier returns the original page without Wayback's banner/rewrites.
    local snap="https://web.archive.org/web/${ts}id_/${orig}"
    local fname
    fname=$(echo "$orig" | sed -E 's#https?://##; s#[^A-Za-z0-9._-]+#_#g')
    curl -fsS "$snap" -o "$OUT/raw/wb_${fname}.html" 2>/dev/null || true
  done
  echo ">> Done. Archived pages saved to $OUT/raw/wb_*.html"
}

if [ "${1:-}" = "--wayback" ]; then
  fetch_wayback
else
  fetch_zendesk || { echo; echo "Primary route failed; trying Wayback ..."; fetch_wayback; }
fi
