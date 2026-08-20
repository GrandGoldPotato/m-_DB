import json, hashlib, urllib.request, datetime, pathlib, re

ROOT = pathlib.Path(__file__).resolve().parents[1]
registry = json.loads((ROOT/"source_registry.json").read_text(encoding="utf-8"))
status_path = ROOT/"source_status.json"
old = {}
if status_path.exists():
    try:
        old = {x["id"]: x for x in json.loads(status_path.read_text(encoding="utf-8"))}
    except Exception:
        old = {}

out=[]
for src in registry:
    row=dict(src)
    row["checked_at"]=datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        req=urllib.request.Request(src["url"],headers={"User-Agent":"Mozilla/5.0 mdang-unitprice-source-watch/1.0"})
        with urllib.request.urlopen(req,timeout=30) as resp:
            body=resp.read()
            text=body.decode("utf-8","ignore")
            row["http_status"]=getattr(resp,"status",200)
            row["etag"]=resp.headers.get("ETag","")
            row["last_modified"]=resp.headers.get("Last-Modified","")
            row["sha256"]=hashlib.sha256(body).hexdigest()
            m=re.search(r"<title[^>]*>(.*?)</title>",text,re.I|re.S)
            row["title"]=re.sub(r"\s+"," ",m.group(1)).strip() if m else ""
            prev=old.get(src["id"],{})
            row["changed"]=bool(prev.get("sha256") and prev.get("sha256") != row["sha256"])
            row["status"]="OK"
    except Exception as e:
        row["status"]="ERROR"
        row["error"]=str(e)
        row["changed"]=False
    out.append(row)

status_path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
