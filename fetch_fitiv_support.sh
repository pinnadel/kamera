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
# Article bodies are converted to clean Markdown. Conversion uses pandoc if
# present, otherwise a self-contained Python 3 converter (standard library
# only -- no extra installs). If neither is available, raw HTML is saved.
#
# Requirements: bash, curl, jq.  (python3 or pandoc recommended for Markdown.)
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

# --- HTML -> Markdown helpers -------------------------------------------------

# A dependency-free Python 3 converter for common Zendesk article HTML.
# The program is written to a temp file so the HTML can be piped in on stdin.
PYCONV=""
init_pyconv() {
  PYCONV="$(mktemp)"
  cat > "$PYCONV" <<'PYEOF'
import sys, re, html
from html.parser import HTMLParser

class MD(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.list_stack, self.skip = [], [], 0
        self.pre = False
        self.href = None

    def nl(self, n=1): self.out.append("\n" * n)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"): self.skip += 1
        elif tag in ("h1","h2","h3","h4","h5","h6"):
            self.nl(2); self.out.append("#" * int(tag[1]) + " ")
        elif tag == "p": self.nl(2)
        elif tag == "br": self.nl(1)
        elif tag in ("strong","b"): self.out.append("**")
        elif tag in ("em","i"): self.out.append("*")
        elif tag == "code" and not self.pre: self.out.append("`")
        elif tag == "pre": self.pre = True; self.nl(2); self.out.append("```\n")
        elif tag == "blockquote": self.nl(2); self.out.append("> ")
        elif tag == "ul": self.list_stack.append("ul"); self.nl(1)
        elif tag == "ol": self.list_stack.append(["ol",0]); self.nl(1)
        elif tag == "li":
            indent = "  " * (len(self.list_stack) - 1)
            self.nl(1)
            if self.list_stack and isinstance(self.list_stack[-1], list):
                self.list_stack[-1][1] += 1
                self.out.append(f"{indent}{self.list_stack[-1][1]}. ")
            else:
                self.out.append(f"{indent}- ")
        elif tag == "a": self.href = a.get("href")
        elif tag == "img":
            self.out.append(f"![{a.get('alt','')}]({a.get('src','')})")

    def handle_endtag(self, tag):
        if tag in ("script","style"): self.skip = max(0, self.skip - 1)
        elif tag in ("h1","h2","h3","h4","h5","h6","p","blockquote"): self.nl(2)
        elif tag in ("strong","b"): self.out.append("**")
        elif tag in ("em","i"): self.out.append("*")
        elif tag == "code" and not self.pre: self.out.append("`")
        elif tag == "pre": self.out.append("\n```"); self.pre = False; self.nl(2)
        elif tag in ("ul","ol"):
            if self.list_stack: self.list_stack.pop()
            self.nl(1)
        elif tag == "a" and self.href is not None:
            self.out.append(f"]({self.href})"); self.href = None

    def handle_data(self, data):
        if self.skip: return
        if self.pre: self.out.append(data); return
        text = re.sub(r"\s+", " ", data)
        if text.strip() == "" and (not self.out or self.out[-1].endswith("\n")): return
        if self.href is not None and "[" not in self.out[-1:] :
            # opening bracket for the link text
            if not (self.out and self.out[-1].endswith("[")):
                self.out.append("[")
        self.out.append(text)

    def result(self):
        s = "".join(self.out)
        s = re.sub(r"\n{3,}", "\n\n", s)        # collapse blank lines
        s = "\n".join(line.rstrip() for line in s.split("\n"))
        return s.strip() + "\n"

raw = sys.stdin.read()
p = MD(); p.feed(raw)
sys.stdout.write(p.result())
PYEOF
}

html_to_md_py() { python3 "$PYCONV"; }

HAVE_PANDOC=0; command -v pandoc  >/dev/null 2>&1 && HAVE_PANDOC=1
HAVE_PY=0;     command -v python3 >/dev/null 2>&1 && HAVE_PY=1
[ "$HAVE_PY" = 1 ] && init_pyconv
trap '[ -n "$PYCONV" ] && rm -f "$PYCONV"' EXIT

write_article() {
  local id="$1" title="$2" body="$3" url="$4"
  local slug
  slug=$(echo "$title" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g')
  slug=${slug:-untitled}

  if [ "$HAVE_PANDOC" = 1 ]; then
    { echo "# $title"; echo; echo "_Source: ${url}_"; echo;
      printf '%s' "$body" | pandoc -f html -t gfm 2>/dev/null; } \
      > "$OUT/articles/${id}-${slug}.md"
  elif [ "$HAVE_PY" = 1 ]; then
    { echo "# $title"; echo; echo "_Source: ${url}_"; echo;
      printf '%s' "$body" | html_to_md_py; } \
      > "$OUT/articles/${id}-${slug}.md"
  else
    { echo "<!-- title: $title -->"; echo "<!-- source: $url -->";
      printf '%s' "$body"; } > "$OUT/articles/${id}-${slug}.html"
  fi
}

# --- Routes -------------------------------------------------------------------

fetch_zendesk() {
  echo ">> Trying Zendesk Help Center API on $HOST ..."
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
    echo "$resp" | jq -c '.articles[]' >> "$OUT/raw/articles.ndjson"
    # Iterate one compact JSON object per line, then pull each field with jq.
    # This keeps article bodies (which contain newlines/tabs) intact.
    while IFS= read -r obj; do
      [ -z "$obj" ] && continue
      local id title url2 body
      id=$(printf '%s' "$obj"   | jq -r '.id')
      title=$(printf '%s' "$obj" | jq -r '.title')
      url2=$(printf '%s' "$obj"  | jq -r '.html_url')
      body=$(printf '%s' "$obj"  | jq -r '.body // ""')
      write_article "$id" "$title" "$body" "$url2"
      total=$((total+1))
    done < <(echo "$resp" | jq -c '.articles[]')
    url=$(echo "$resp" | jq -r '.next_page // empty')
    page=$((page+1))
  done

  if [ "$total" -eq 0 ]; then echo "   no articles returned"; return 1; fi
  echo ">> Done. Saved $total articles to $OUT/articles/  (raw JSON in $OUT/raw/)"
}

fetch_wayback() {
  echo ">> Falling back to the Wayback Machine for $HOST ..."
  local cdx="$OUT/raw/wayback_index.json"
  curl -fsS "http://web.archive.org/cdx/search/cdx?url=${HOST}/*&output=json&fl=timestamp,original&collapse=urlkey&filter=statuscode:200" \
    -o "$cdx"
  local n; n=$(jq 'length - 1' "$cdx")
  echo "   found $n archived URLs; downloading snapshots ..."
  jq -r '.[1:][] | "\(.[0])\t\(.[1])"' "$cdx" | while IFS=$'\t' read -r ts orig; do
    local snap="https://web.archive.org/web/${ts}id_/${orig}"
    local fname; fname=$(echo "$orig" | sed -E 's#https?://##; s#[^A-Za-z0-9._-]+#_#g')
    curl -fsS "$snap" -o "$OUT/raw/wb_${fname}.html" 2>/dev/null || true
  done
  echo ">> Done. Archived pages saved to $OUT/raw/wb_*.html"
}

if [ "${1:-}" = "--wayback" ]; then
  fetch_wayback
else
  fetch_zendesk || { echo; echo "Primary route failed; trying Wayback ..."; fetch_wayback; }
fi
