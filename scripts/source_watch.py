import json
import hashlib
import urllib.request
import urllib.error
import datetime
import pathlib
import re
import ssl
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parents[1]

registry = json.loads(
    (ROOT / "source_registry.json").read_text(encoding="utf-8")
)

status_path = ROOT / "source_status.json"

old = {}

if status_path.exists():
    try:
        old = {
            x["id"]: x
            for x in json.loads(
                status_path.read_text(encoding="utf-8")
            )
        }
    except Exception:
        old = {}

results = []


def download(url):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent":
            "Mozilla/5.0 mdang-unitprice-source-watch/1.0"
        }
    )

    try:
        # 1차: 정상 SSL 검증
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read(), resp, "VERIFIED"

    except urllib.error.URLError as e:

        host = urlparse(url).hostname or ""

        # CODIL 인증서 체인 오류에 한해서만 예외 처리
        if (
            host.endswith("codil.or.kr")
            and isinstance(e.reason, ssl.SSLCertVerificationError)
        ):
            context = ssl._create_unverified_context()

            with urllib.request.urlopen(
                req,
                timeout=30,
                context=context
            ) as resp:
                return resp.read(), resp, "CODIL_SSL_FALLBACK"

        raise


for src in registry:

    row = dict(src)

    row["checked_at"] = (
        datetime.datetime
        .now(datetime.timezone.utc)
        .isoformat()
    )

    try:

        body, resp, tls_mode = download(src["url"])

        text = body.decode("utf-8", "ignore")

        row["http_status"] = getattr(resp, "status", 200)

        row["etag"] = resp.headers.get("ETag", "")

        row["last_modified"] = resp.headers.get(
            "Last-Modified", ""
        )

        row["sha256"] = hashlib.sha256(body).hexdigest()

        row["tls_mode"] = tls_mode

        match = re.search(
            r"<title[^>]*>(.*?)</title>",
            text,
            re.I | re.S
        )

        if match:
            row["title"] = re.sub(
                r"\s+",
                " ",
                match.group(1)
            ).strip()
        else:
            row["title"] = ""

        previous = old.get(src["id"], {})

        row["changed"] = bool(
            previous.get("sha256")
            and previous.get("sha256") != row["sha256"]
        )

        if tls_mode == "CODIL_SSL_FALLBACK":
            row["status"] = "OK"
            row["warning"] = (
                "CODIL SSL 인증서 체인 검증 실패로 "
                "해당 도메인에 한해 SSL fallback 사용"
            )
        else:
            row["status"] = "OK"

    except Exception as e:

        row["status"] = "ERROR"
        row["error"] = str(e)
        row["changed"] = False

    results.append(row)


status_path.write_text(
    json.dumps(
        results,
        ensure_ascii=False,
        indent=2
    ),
    encoding="utf-8"
)
