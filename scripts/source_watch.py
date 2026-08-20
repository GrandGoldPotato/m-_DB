import json
import hashlib
import urllib.request
import urllib.error
import http.cookiejar
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

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36 mdang-watch/2.2"
ATTACH_EXTS = (".pdf",".hwp",".hwpx",".xls",".xlsx",".zip",".csv",".txt")

_cookiejar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookiejar))

def norm(s):
    s = html.unescape(str(s or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

class AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors=[]
        self.all_text=[]
        self._ina=False
        self._attrs={}
        self._parts=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower()=="a":
            self._ina=True
            self._attrs=dict(attrs)
            self._parts=[]
    def handle_data(self,data):
        self.all_text.append(data)
        if self._ina:
            self._parts.append(data)
    def handle_endtag(self,tag):
        if tag.lower()=="a" and self._ina:
            self.anchors.append({
                "text":norm(" ".join(self._parts)),
                "href":self._attrs.get("href","") or "",
                "onclick":self._attrs.get("onclick","") or ""
            })
            self._ina=False
            self._attrs={}
            self._parts=[]

def browser_request(url, referer=None):
    headers={
        "User-Agent":UA,
        "Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language":"ko-KR,ko;q=0.9,en;q=0.7",
        "Cache-Control":"no-cache",
        "Pragma":"no-cache",
    }
    if referer:
        headers["Referer"]=referer
    return urllib.request.Request(url,headers=headers)

def fetch(url, referer=None):
    req=browser_request(url,referer)
    host=urlsplit(url).hostname or ""
    try:
        with _opener.open(req,timeout=35) as resp:
            return resp.read(),dict(resp.headers),"VERIFIED",getattr(resp,"status",200)
    except urllib.error.URLError as e:
        if host.endswith("codil.or.kr") and isinstance(e.reason,ssl.SSLCertVerificationError):
            ctx=ssl._create_unverified_context()
            opener=urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(_cookiejar),
                urllib.request.HTTPSHandler(context=ctx)
            )
            with opener.open(req,timeout=35) as resp:
                return resp.read(),dict(resp.headers),"CODIL_SSL_FALLBACK",getattr(resp,"status",200)
        raise

def decode(body,headers):
    ct=headers.get("Content-Type","")
    m=re.search(r"charset=([A-Za-z0-9._-]+)",ct,re.I)
    tries=[]
    if m: tries.append(m.group(1))
    tries += ["utf-8","cp949","euc-kr"]
    for enc in tries:
        try:
            return body.decode(enc)
        except Exception:
            pass
    return body.decode("utf-8","ignore")

def canonical(url):
    if not url: return ""
    url=re.sub(r";jsessionid=[^?/#]+","",url,flags=re.I)
    p=urlsplit(url)
    q=urlencode(sorted(parse_qsl(p.query,keep_blank_values=True)))
    return urlunsplit((p.scheme,p.netloc,p.path,q,""))

def parse_html(text):
    p=AnchorParser()
    p.feed(text)
    return p

def attachment_names(text,base):
    p=parse_html(text)
    out=set()
    for a in p.anchors:
        name=norm(a["text"])
        href=urljoin(base,a["href"])
        low=(name+" "+href).lower()
        if any(ext in low for ext in ATTACH_EXTS) or "download" in low:
            if name and name.lower() not in ("첨부파일","파일","image"):
                out.add(name)
    return sorted(out)

def make_fp(title,post_id,files):
    payload={"title":norm(title),"post_id":post_id,"attachments":files}
    return hashlib.sha256(
        json.dumps(payload,ensure_ascii=False,sort_keys=True).encode("utf-8")
    ).hexdigest()

def codil_latest(src):
    body,headers,tls,http=fetch(src["url"])
    text=decode(body,headers)
    title_re=re.compile(src["title_regex"],re.I)
    matches=list(title_re.finditer(norm(text)))
    if not matches:
        raise RuntimeError("CODIL 목록에서 최신 제목을 찾지 못했습니다.")
    latest_title=matches[0].group(0)

    # CODIL 게시물 링크는 href보다 javascript/onclick에 nttId가 숨는 경우가 있어
    # 제목 주변 원문 HTML에서 nttId 후보를 직접 찾는다.
    pos=text.find(latest_title)
    windows=[]
    if pos>=0:
        windows.append(text[max(0,pos-3000):pos+3000])
    windows.append(text)
    ntt=None
    for w in windows:
        candidates=re.findall(r"(?:nttId(?:=|['\" :,])+|fnc?\w*\(['\"]?)(\d{5,8})",w,re.I)
        if candidates:
            ntt=candidates[0]
            break
    if not ntt:
        # Known current IDs are NOT used as the change detector; only a fallback
        # to open the current detail if CODIL hides the JS parameter unusually.
        fallback={
          "BBSMSTR_900000000202":"13261",
          "BBSMSTR_900000000204":"13281",
        }
        ntt=fallback.get(src["bbs_id"])
    if not ntt:
        raise RuntimeError("CODIL 최신 게시물의 nttId를 추출하지 못했습니다.")

    detail=f"https://www.codil.or.kr/helpdesk/read.do?bbsId={src['bbs_id']}&nttId={ntt}"
    dbody,dheaders,dtls,dhttp=fetch(detail,src["url"])
    dtext=decode(dbody,dheaders)
    # Detail title is authoritative if available.
    m=title_re.search(norm(dtext))
    if m:
        latest_title=m.group(0)
    files=attachment_names(dtext,detail)
    pid="nttId="+ntt
    return {
      "http_status":http,
      "detail_http_status":dhttp,
      "tls_mode":tls if tls!="VERIFIED" else dtls,
      "latest_title":latest_title,
      "latest_url":canonical(detail),
      "latest_post_id":pid,
      "attachments":files,
      "fingerprint":make_fp(latest_title,pid,files)
    }

def cak_latest(src):
    # Warm up cookie/session first; some CAK list requests return 404 to bare urllib.
    try:
        fetch("https://info.cak.or.kr/")
    except Exception:
        pass

    body,headers,tls,http=fetch(src["url"],"https://info.cak.or.kr/")
    text=decode(body,headers)
    p=parse_html(text)
    title_re=re.compile(src["title_regex"],re.I)

    candidates=[]
    for a in p.anchors:
        title=norm(a["text"])
        if title_re.fullmatch(title) or title_re.search(title):
            combo=a["href"]+" "+a["onclick"]
            m=re.search(r"article_seq[=:'\" (),]+(\d+)",combo,re.I)
            if not m:
                m=re.search(r"\b(\d{5,9})\b",combo)
            if m:
                candidates.append((title,m.group(1)))

    if not candidates:
        # Raw HTML fallback.
        flat=norm(text)
        tm=title_re.search(flat)
        if not tm:
            raise RuntimeError("대한건설협회 목록에서 최신 임금 보고서 제목을 찾지 못했습니다.")
        latest_title=tm.group(0)
        pos=text.find(latest_title)
        w=text[max(0,pos-4000):pos+4000] if pos>=0 else text
        m=re.search(r"article_seq[=:'\" (),]+(\d+)",w,re.I)
        if not m:
            raise RuntimeError("대한건설협회 최신 보고서 article_seq를 추출하지 못했습니다.")
        article=m.group(1)
    else:
        latest_title,article=candidates[0]

    detail=("https://info.cak.or.kr/lay1/bbs/S1T41C42/A/14/view.do"
            f"?article_seq={article}&mode=view&rows=10")
    dbody,dheaders,dtls,dhttp=fetch(detail,src["url"])
    dtext=decode(dbody,dheaders)
    dm=title_re.search(norm(dtext))
    if dm:
        latest_title=dm.group(0)
    files=attachment_names(dtext,detail)
    pid="article_seq="+article
    return {
      "http_status":http,
      "detail_http_status":dhttp,
      "tls_mode":tls,
      "latest_title":latest_title,
      "latest_url":canonical(detail),
      "latest_post_id":pid,
      "attachments":files,
      "fingerprint":make_fp(latest_title,pid,files)
    }

def pps_known(src):
    detail=src["known_detail_url"]
    body,headers,tls,http=fetch(detail)
    text=decode(body,headers)
    files=attachment_names(text,detail)
    m=re.search(r"bbsSn=(\d+)",detail)
    pid="bbsSn="+m.group(1) if m else canonical(detail)
    return {
      "http_status":http,"tls_mode":tls,
      "latest_title":src["known_title"],
      "latest_url":canonical(detail),
      "latest_post_id":pid,
      "attachments":files,
      "fingerprint":make_fp(src["known_title"],pid,files)
    }

def rule_page(src):
    body,headers,tls,http=fetch(src["url"])
    text=decode(body,headers)
    full=norm(text)
    m=re.search(
      r"\[시행\s*([0-9. ]+)\].{0,150}?"
      r"\[행정안전부예규\s*제([0-9]+)호,\s*([0-9. ]+),\s*([^\]]+)\]",
      full
    )
    if not m:
        raise RuntimeError("현행 예규 번호/시행일을 추출하지 못했습니다.")
    current=(f"시행 {norm(m.group(1))} / 행정안전부예규 제{m.group(2)}호 / "
             f"발령 {norm(m.group(3))} / {norm(m.group(4))}")
    pid="예규 제"+m.group(2)+"호"
    fp=make_fp(src["rule_name"],pid,[current])
    return {
      "http_status":http,"tls_mode":tls,
      "latest_title":src["rule_name"],
      "latest_url":canonical(src["url"]),
      "latest_post_id":pid,
      "current_rule":current,
      "attachments":[],
      "fingerprint":fp
    }

def api_catalog(src):
    body,headers,tls,http=fetch(src["url"])
    text=decode(body,headers)
    m=re.search(r"<title[^>]*>(.*?)</title>",text,re.I|re.S)
    title=norm(m.group(1)) if m else src["name"]
    pid="API_CATALOG"
    return {
      "http_status":http,"tls_mode":tls,
      "latest_title":title,
      "latest_url":canonical(src["url"]),
      "latest_post_id":pid,
      "attachments":[],
      "fingerprint":make_fp(title,pid,[]),
      "api_status":"SERVICE_KEY_AND_ITEM_MAPPING_REQUIRED"
    }

def load_old():
    if not STATUS_PATH.exists(): return {}
    try:
        return {x["id"]:x for x in json.loads(STATUS_PATH.read_text(encoding="utf-8")) if x.get("id")}
    except Exception:
        return {}

registry=json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
old=load_old()
results=[]

for src in registry:
    row={
      "id":src["id"],"name":src["name"],"grade":src["grade"],
      "mode":src["mode"],"url":src["url"],
      "checked_at":datetime.datetime.now(datetime.timezone.utc).isoformat()
    }
    try:
        if src["mode"]=="codil_latest":
            st=codil_latest(src)
        elif src["mode"]=="cak_latest":
            st=cak_latest(src)
        elif src["mode"]=="latest_or_known":
            st=pps_known(src)
        elif src["mode"]=="rule_page":
            st=rule_page(src)
        elif src["mode"]=="api_catalog":
            st=api_catalog(src)
        else:
            raise RuntimeError("지원하지 않는 mode: "+str(src["mode"]))

        row.update(st)
        prev=old.get(src["id"],{})
        migrated=prev.get("watch_version")!="2.2"
        prev_fp=prev.get("fingerprint")
        row["watch_version"]="2.2"
        row["baseline_reset"]=migrated or not bool(prev_fp)
        row["changed"]=False if row["baseline_reset"] else (prev_fp != row["fingerprint"])
        row["status"]="OK"

        if row.get("tls_mode")=="CODIL_SSL_FALLBACK":
            row["warning"]="CODIL SSL 인증서 체인 검증 실패로 해당 도메인에 한해 SSL fallback 사용"
        if src["mode"]=="api_catalog":
            row["note"]="현재는 API 카탈로그 감시 단계입니다. 실제 가격 자동갱신은 서비스키·품목코드 매핑 후 연결합니다."

    except Exception as e:
        row["watch_version"]="2.2"
        row["status"]="ERROR"
        row["changed"]=False
        row["error"]=str(e)

    results.append(row)

STATUS_PATH.write_text(
    json.dumps(results,ensure_ascii=False,indent=2),
    encoding="utf-8"
)
