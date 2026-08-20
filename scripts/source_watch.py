import json, hashlib, urllib.request, urllib.error, datetime, pathlib, re, ssl, html
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit, parse_qsl, urlencode, parse_qs

ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "source_registry.json"
STATUS_PATH = ROOT / "source_status.json"
UA = "Mozilla/5.0 mdang-unitprice-source-watch/2.1"

ATTACH_EXTS = (".pdf",".hwp",".hwpx",".xls",".xlsx",".zip",".csv",".txt")

def norm(s):
    s = html.unescape(str(s or ""))
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

class Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors=[]
        self.all_text=[]
        self._a=False; self._attrs={}; self._parts=[]
    def handle_starttag(self, tag, attrs):
        if tag.lower()=="a":
            self._a=True
            self._attrs=dict(attrs)
            self._parts=[]
    def handle_data(self, data):
        self.all_text.append(data)
        if self._a: self._parts.append(data)
    def handle_endtag(self, tag):
        if tag.lower()=="a" and self._a:
            self.anchors.append({
                "text": norm(" ".join(self._parts)),
                "href": self._attrs.get("href","") or "",
                "onclick": self._attrs.get("onclick","") or ""
            })
            self._a=False; self._attrs={}; self._parts=[]

def fetch(url):
    req=urllib.request.Request(url, headers={"User-Agent":UA})
    host=urlsplit(url).hostname or ""
    try:
        with urllib.request.urlopen(req,timeout=35) as resp:
            return resp.read(), dict(resp.headers), "VERIFIED", getattr(resp,"status",200)
    except urllib.error.URLError as e:
        if host.endswith("codil.or.kr") and isinstance(e.reason, ssl.SSLCertVerificationError):
            ctx=ssl._create_unverified_context()
            with urllib.request.urlopen(req,timeout=35,context=ctx) as resp:
                return resp.read(), dict(resp.headers), "CODIL_SSL_FALLBACK", getattr(resp,"status",200)
        raise

def decode(body, headers):
    ct=headers.get("Content-Type","")
    m=re.search(r"charset=([A-Za-z0-9._-]+)",ct,re.I)
    encs=[m.group(1)] if m else []
    encs += ["utf-8","cp949","euc-kr"]
    for enc in encs:
        try: return body.decode(enc)
        except Exception: pass
    return body.decode("utf-8","ignore")

def parse(text):
    p=Parser(); p.feed(text); return p

def canonical(url):
    if not url: return ""
    url=re.sub(r";jsessionid=[^?/#]+","",url,flags=re.I)
    sp=urlsplit(url)
    q=urlencode(sorted(parse_qsl(sp.query,keep_blank_values=True)))
    return urlunsplit((sp.scheme,sp.netloc,sp.path,q,""))

def title_match(title, src):
    t=norm(title)
    if not t: return False
    if any(k not in t for k in src.get("include_all",[])): return False
    inc_any=src.get("include_any",[])
    if inc_any and not any(k in t for k in inc_any): return False
    if any(k in t for k in src.get("exclude_any",[])): return False
    return True

def href_ok(a,src):
    rules=src.get("required_href_any",[])
    if not rules: return True
    combo=(a.get("href","")+" "+a.get("onclick",""))
    return any(x in combo for x in rules)

def extract_id(url_or_code, params):
    for key in params:
        m=re.search(r"(?:[?&])"+re.escape(key)+r"=([^&#'\" )]+)",url_or_code,re.I)
        if m: return key+"="+m.group(1)
    return ""

def detail_from_anchor(a, src):
    href=a.get("href","")
    onclick=a.get("onclick","")
    base=src.get("detail_base",src["url"])
    if href and not href.lower().startswith("javascript") and href not in ("#","javascript:void(0);"):
        return canonical(urljoin(base,href))
    # PPS often stores bbsSn in onclick/javascript rather than href.
    combo=href+" "+onclick
    m=re.search(r"\b(\d{10})\b",combo)
    if m and "pps.go.kr" in base:
        return f"https://www.pps.go.kr/kor/bbs/view.do?bbsSn={m.group(1)}&key=00038"
    return ""

def attachments(text, base):
    p=parse(text); out=set()
    for a in p.anchors:
        nm=norm(a["text"]); h=urljoin(base,a["href"])
        low=(nm+" "+h).lower()
        if any(ext in low for ext in ATTACH_EXTS) or "download" in low:
            if nm and nm.lower() not in ("첨부파일","파일","image"):
                out.add(nm)
    return sorted(out)

def post_state_from_detail(title, url, src):
    body,headers,tls,http=fetch(url)
    text=decode(body,headers)
    files=attachments(text,url)
    pid=extract_id(url,src.get("id_params",[])) or canonical(url)
    ident={"post_id":pid,"title":norm(title),"attachments":files}
    fp=hashlib.sha256(json.dumps(ident,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return {
      "http_status":http,"tls_mode":tls,"latest_title":norm(title),
      "latest_url":canonical(url),"latest_post_id":pid,"attachments":files,
      "fingerprint":fp
    }

def latest_post(src):
    body,headers,tls,http=fetch(src["url"])
    text=decode(body,headers); p=parse(text)
    for a in p.anchors:
        if title_match(a["text"],src) and href_ok(a,src):
            u=detail_from_anchor(a,src)
            if u:
                st=post_state_from_detail(a["text"],u,src)
                st["list_http_status"]=http
                if tls!="VERIFIED": st["tls_mode"]=tls
                return st
    raise RuntimeError("목록 페이지에서 조건에 맞는 실제 상세 게시물을 찾지 못했습니다.")

def latest_or_known(src):
    # 1) First page: if a new matching notice appears, pick it immediately.
    try:
        body,headers,tls,http=fetch(src["url"])
        text=decode(body,headers); p=parse(text)
        for a in p.anchors:
            if title_match(a["text"],src):
                u=detail_from_anchor(a,src)
                if u:
                    st=post_state_from_detail(a["text"],u,src)
                    st["list_http_status"]=http
                    return st
    except Exception:
        pass
    # 2) If current relevant notice has moved off page 1, monitor its known official detail.
    return post_state_from_detail(src["known_title"],src["known_detail_url"],src)

def rule_page(src):
    body,headers,tls,http=fetch(src["url"])
    text=decode(body,headers); p=parse(text)
    full=norm(" ".join(p.all_text))
    # Capture current rule version, e.g. [시행 2026. 7. 1.] [행정안전부예규 제372호, 2026. 6. 29., 일부개정]
    pat=(re.escape(src["rule_name"]) + r".{0,250}?"
         r"\[시행\s*([0-9. ]+)\].{0,80}?"
         r"\[행정안전부예규\s*제([0-9]+)호,\s*([0-9. ]+),\s*([^\]]+)\]")
    m=re.search(pat,full)
    if not m:
        # fallback: search version without requiring immediate rule name proximity
        m=re.search(r"\[시행\s*([0-9. ]+)\].{0,100}?\[행정안전부예규\s*제([0-9]+)호,\s*([0-9. ]+),\s*([^\]]+)\]",full)
    if not m:
        raise RuntimeError("국가법령정보센터에서 현행 예규 번호/시행일을 추출하지 못했습니다.")
    current=f"시행 {norm(m.group(1))} / 행정안전부예규 제{m.group(2)}호 / 발령 {norm(m.group(3))} / {norm(m.group(4))}"
    ident={"rule":src["rule_name"],"current":current}
    fp=hashlib.sha256(json.dumps(ident,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return {
      "http_status":http,"tls_mode":tls,"latest_title":src["rule_name"],
      "latest_url":canonical(src["url"]),"latest_post_id":"예규 제"+m.group(2)+"호",
      "current_rule":current,"attachments":[],"fingerprint":fp
    }

def api_catalog(src):
    body,headers,tls,http=fetch(src["url"])
    text=decode(body,headers)
    m=re.search(r"<title[^>]*>(.*?)</title>",text,re.I|re.S)
    title=norm(m.group(1)) if m else src["name"]
    ident={"url":canonical(src["url"]),"title":title}
    fp=hashlib.sha256(json.dumps(ident,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return {
      "http_status":http,"tls_mode":tls,"latest_title":title,
      "latest_url":canonical(src["url"]),"latest_post_id":"API_CATALOG",
      "attachments":[],"fingerprint":fp,
      "api_status":"SERVICE_KEY_AND_ITEM_MAPPING_REQUIRED"
    }

def load_old():
    if not STATUS_PATH.exists(): return {}
    try:
        return {x["id"]:x for x in json.loads(STATUS_PATH.read_text(encoding="utf-8")) if x.get("id")}
    except Exception:
        return {}

registry=json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
old=load_old(); results=[]
for src in registry:
    row={"id":src["id"],"name":src["name"],"grade":src["grade"],"mode":src["mode"],
         "url":src["url"],"checked_at":datetime.datetime.now(datetime.timezone.utc).isoformat()}
    try:
        if src["mode"]=="latest_post": st=latest_post(src)
        elif src["mode"]=="latest_or_known": st=latest_or_known(src)
        elif src["mode"]=="rule_page": st=rule_page(src)
        elif src["mode"]=="api_catalog": st=api_catalog(src)
        else: raise RuntimeError("지원하지 않는 mode")
        row.update(st)
        prev=old.get(src["id"],{})
        prevfp=prev.get("fingerprint")
        # Ver.2.1 migration resets baseline once when mode/logic changes.
        migrated = prev.get("watch_version") != "2.1"
        row["watch_version"]="2.1"
        row["baseline_reset"]=migrated or not bool(prevfp)
        row["changed"]=False if row["baseline_reset"] else (prevfp != row["fingerprint"])
        row["status"]="OK"
        if row.get("tls_mode")=="CODIL_SSL_FALLBACK":
            row["warning"]="CODIL SSL 인증서 체인 검증 실패로 해당 도메인에 한해 SSL fallback 사용"
        if src["mode"]=="api_catalog":
            row["note"]="현재는 API 카탈로그 접근성 감시 단계입니다. 실제 자재가격 자동갱신은 서비스키와 품목코드 매핑 후 연결합니다."
    except Exception as e:
        row["watch_version"]="2.1"; row["status"]="ERROR"; row["changed"]=False; row["error"]=str(e)
    results.append(row)

STATUS_PATH.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf-8")
