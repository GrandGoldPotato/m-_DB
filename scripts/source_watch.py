import json
import hashlib
import urllib.request
import urllib.error
import datetime
import pathlib
import re
import ssl
import html
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode

ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "source_registry.json"
STATUS_PATH = ROOT / "source_status.json"

USER_AGENT = "Mozilla/5.0 mdang-unitprice-source-watch/2.0"
ATTACH_EXTS = (".pdf", ".hwp", ".hwpx", ".xls", ".xlsx", ".zip", ".csv", ".txt")


class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = []
        self._in_a = False
        self._href = ""
        self._parts = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._in_a = True
            self._href = dict(attrs).get("href", "") or ""
            self._parts = []

    def handle_data(self, data):
        if self._in_a:
            self._parts.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._in_a:
            text = normalize_text(" ".join(self._parts))
            if text:
                self.anchors.append({"text": text, "href": self._href})
            self._in_a = False
            self._href = ""
            self._parts = []


def normalize_text(s):
    s = html.unescape(str(s or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def canonical_url(url):
    """Remove session IDs and sort query parameters for a stable identity."""
    if not url:
        return ""
    url = re.sub(r";jsessionid=[^?/#]+", "", url, flags=re.I)
    parts = urlsplit(url)
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    host = urlsplit(url).hostname or ""

    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            return resp.read(), dict(resp.headers), "VERIFIED", getattr(resp, "status", 200)
    except urllib.error.URLError as e:
        if host.endswith("codil.or.kr") and isinstance(e.reason, ssl.SSLCertVerificationError):
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=35, context=ctx) as resp:
                return resp.read(), dict(resp.headers), "CODIL_SSL_FALLBACK", getattr(resp, "status", 200)
        raise


def decode_body(body, headers):
    charset = "utf-8"
    content_type = headers.get("Content-Type", "")
    m = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, re.I)
    if m:
        charset = m.group(1)
    for enc in (charset, "utf-8", "cp949", "euc-kr"):
        try:
            return body.decode(enc)
        except Exception:
            pass
    return body.decode("utf-8", "ignore")


def parse_anchors(text):
    p = AnchorParser()
    p.feed(text)
    return p.anchors


def title_matches(text, src):
    t = normalize_text(text)
    for kw in src.get("include_all", []):
        if kw not in t:
            return False
    for kw in src.get("exclude_any", []):
        if kw in t:
            return False
    return bool(t)


def extract_stable_id(url):
    """Prefer the site's stable post identifier over the full URL."""
    for key in ("nttId", "article_seq", "bbsSn"):
        m = re.search(r"(?:[?&])" + re.escape(key) + r"=([^&#]+)", url, re.I)
        if m:
            return key + "=" + m.group(1)
    # Some boards use path IDs; canonical URL is the fallback.
    return canonical_url(url)


def attachment_names(text, base_url):
    anchors = parse_anchors(text)
    found = set()
    for a in anchors:
        name = normalize_text(a["text"])
        href = urljoin(base_url, a["href"])
        low = (name + " " + href).lower()
        if any(ext in low for ext in ATTACH_EXTS) or "download" in low or "file" in low:
            # Ignore generic image/icon labels.
            if name and name.lower() not in ("첨부파일", "image", "파일"):
                found.add(name)
    return sorted(found)


def latest_post_state(src):
    body, headers, tls_mode, http_status = fetch(src["url"])
    text = decode_body(body, headers)
    anchors = parse_anchors(text)

    selected = None
    for a in anchors:
        if title_matches(a["text"], src):
            href = urljoin(src.get("detail_base", src["url"]), a["href"])
            selected = {
                "title": normalize_text(a["text"]),
                "url": canonical_url(href),
            }
            break

    if not selected:
        raise RuntimeError("목록 페이지에서 조건에 맞는 최신 게시물을 찾지 못했습니다.")

    selected["post_id"] = extract_stable_id(selected["url"])

    # Detail page lets us detect corrigenda / replacement attachments on the same post.
    detail_body, detail_headers, detail_tls, detail_status = fetch(selected["url"])
    detail_text = decode_body(detail_body, detail_headers)
    files = attachment_names(detail_text, selected["url"])

    identity_obj = {
        "post_id": selected["post_id"],
        "title": selected["title"],
        "attachments": files,
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return {
        "http_status": http_status,
        "tls_mode": tls_mode if tls_mode != "VERIFIED" else detail_tls,
        "latest_title": selected["title"],
        "latest_url": selected["url"],
        "latest_post_id": selected["post_id"],
        "attachments": files,
        "fingerprint": fingerprint,
        "detail_http_status": detail_status,
    }


def api_catalog_state(src):
    body, headers, tls_mode, http_status = fetch(src["url"])
    text = decode_body(body, headers)

    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    page_title = normalize_text(m.group(1)) if m else src["name"]

    # Catalog page itself is not the actual price feed.
    identity_obj = {
        "url": canonical_url(src["url"]),
        "title": page_title,
    }
    fingerprint = hashlib.sha256(
        json.dumps(identity_obj, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return {
        "http_status": http_status,
        "tls_mode": tls_mode,
        "latest_title": page_title,
        "latest_url": canonical_url(src["url"]),
        "latest_post_id": "API_CATALOG",
        "attachments": [],
        "fingerprint": fingerprint,
        "api_status": "SERVICE_KEY_AND_ITEM_MAPPING_REQUIRED",
    }


def load_old():
    if not STATUS_PATH.exists():
        return {}
    try:
        rows = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        return {r.get("id"): r for r in rows if r.get("id")}
    except Exception:
        return {}


registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
old = load_old()
results = []

for src in registry:
    row = {
        "id": src["id"],
        "name": src["name"],
        "grade": src["grade"],
        "mode": src["mode"],
        "url": src["url"],
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    try:
        if src["mode"] == "latest_post":
            state = latest_post_state(src)
        elif src["mode"] == "api_catalog":
            state = api_catalog_state(src)
        else:
            raise RuntimeError("지원하지 않는 mode: " + str(src["mode"]))

        row.update(state)
        previous = old.get(src["id"], {})
        prev_fp = previous.get("fingerprint")

        # First run after V2 conversion establishes a clean baseline.
        row["baseline_reset"] = not bool(prev_fp)
        row["changed"] = bool(prev_fp and prev_fp != row["fingerprint"])
        row["status"] = "OK"

        if row.get("tls_mode") == "CODIL_SSL_FALLBACK":
            row["warning"] = (
                "CODIL SSL 인증서 체인 검증 실패로 해당 도메인에 한해 SSL fallback 사용"
            )

        if src["mode"] == "api_catalog":
            row["note"] = (
                "현재는 API 카탈로그 접근성만 감시합니다. 실제 자재가격 자동갱신은 "
                "공공데이터포털 서비스키와 상수도 자재 품목코드 매핑 후 별도 연결합니다."
            )

    except Exception as e:
        row["status"] = "ERROR"
        row["changed"] = False
        row["error"] = str(e)

    results.append(row)

STATUS_PATH.write_text(
    json.dumps(results, ensure_ascii=False, indent=2),
    encoding="utf-8"
)
