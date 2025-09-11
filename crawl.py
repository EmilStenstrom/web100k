#!/usr/bin/env python3
"""
live_homepages.py — Download decoded homepage HTML for a list of domains.
Resumes automatically by skipping existing .html or .error files.
Writes <domain>.error if domain repeatedly fails.

Features:
- Tries https://www.<d>/, https://<d>/, http://www.<d>/, http://<d>/
- Decodes gzip/deflate/brotli automatically, with safe gunzip fallback
- Saves only if looks like HTML
- Resumes: skips domains with existing .html or .error
- On failure, writes <domain>.error so retries won’t waste time
- Worker futures are bounded by a global max-seconds timeout
"""

import argparse, concurrent.futures as cf, os, re, sys, time, random
from pathlib import Path
from typing import Optional, Tuple, List
import gzip, zlib, requests

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from requests.exceptions import RequestException, SSLError, Timeout, ConnectionError as ReqConnectionError
from tqdm import tqdm

CANDIDATE_URLS = [
    "https://www.{d}/",
    "https://{d}/",
    "http://www.{d}/",
    "http://{d}/",
]

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

def choose_ua() -> str:
    return random.choice(UA_POOL)

def sanitize_filename(name: str) -> str:
    return re.sub(r"[^\w\.\-]+", "_", name.strip())

def looks_like_html(decoded: bytes) -> bool:
    if not decoded:
        return False
    sample = decoded[:4096]
    printable = sum(32 <= b <= 126 or b in (9,10,13) for b in sample)
    if printable / max(1, len(sample)) < 0.6:
        return False
    s = sample.decode("utf-8", "ignore").lower()
    return any(tag in s for tag in ("<!doctype", "<html", "<head", "<title", "<meta"))

def safe_gunzip(raw: bytes) -> bytes:
    """Try gzip; if broken, try zlib; else return raw."""
    try:
        return gzip.decompress(raw)
    except Exception:
        try:
            return zlib.decompress(raw, 16 + zlib.MAX_WBITS)
        except Exception:
            return raw

def decode_content(enc: str, raw: bytes) -> Tuple[Optional[bytes], Optional[str]]:
    enc = (enc or "").lower()
    try:
        if not enc or enc == "identity":
            return raw, None
        if "gzip" in enc or "x-gzip" in enc:
            try:
                return safe_gunzip(raw), None
            except Exception as e:
                return raw, f"gzip-bad:{type(e).__name__}"
        if "deflate" in enc:
            try:
                return zlib.decompress(raw), None
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS), None
        if "br" in enc or "brotli" in enc:
            try:
                import brotli
                return brotli.decompress(raw), None
            except Exception as e:
                try:
                    return safe_gunzip(raw), f"br-fallback-gzip:{type(e).__name__}"
                except Exception:
                    return raw, f"br-fallback-raw:{type(e).__name__}"
        return None, f"enc:unknown({enc})"
    except Exception as e:
        return None, f"enc:decode-error:{type(e).__name__}"

def build_session(timeout: int, retries: int, ipv4: bool) -> requests.Session:
    sess = requests.Session()
    retry = Retry(
        total=retries,
        connect=retries,
        read=retries,
        status=retries,
        status_forcelist=[429,500,502,503,504],
        backoff_factor=0.7,
        respect_retry_after_header=True,
        allowed_methods=frozenset(["GET","HEAD"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=100, pool_maxsize=100)
    sess.mount("http://", adapter); sess.mount("https://", adapter)
    sess.headers.update({
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "en;q=0.9",
        "Connection": "close",
    })
    sess.request_timeout = timeout
    if ipv4:
        import socket, urllib3.util.connection as urllib3_cn
        def _af(): return socket.AF_INET
        urllib3_cn.allowed_gai_family = _af  # type: ignore
    return sess

def fetch_homepage(domain: str, timeout: int, session: requests.Session) -> Tuple[bool,str,Optional[bytes]]:
    last_err = ""
    for url_tpl in CANDIDATE_URLS:
        url = url_tpl.format(d=domain)
        ua = choose_ua()
        try:
            resp = session.get(url, allow_redirects=True, timeout=timeout, headers={"User-Agent": ua})
            if resp.status_code == 429:
                time.sleep(1.0)
                resp = session.get(url, allow_redirects=True, timeout=timeout, headers={"User-Agent": choose_ua()})
            decoded, dec_err = decode_content(resp.headers.get("Content-Encoding"), resp.content)
            if decoded is None:
                last_err = dec_err or "decode-failed"; continue
            if 200 <= resp.status_code < 300 and looks_like_html(decoded):
                return True, str(resp.url), decoded
            last_err = f"status:{resp.status_code}"
        except (Timeout, SSLError) as e:
            last_err = f"timeout:{e.__class__.__name__}"; continue
        except (ReqConnectionError, RequestException) as e:
            last_err = f"net:{e.__class__.__name__}"; continue
    return False, last_err, None

def read_domains(path: str, limit: Optional[int]=None) -> List[str]:
    with open(path,"r",encoding="utf-8") as f:
        doms=[line.strip().lower() for line in f if line.strip() and not line.startswith("#")]
    return doms[:limit] if limit else doms

def list_already_handled(out_dir: Path) -> set[str]:
    done=set()
    for fn in os.listdir(out_dir):
        if fn.endswith(".html") or fn.endswith(".error"):
            base=fn.rsplit(".",1)[0]
            done.add(base.lower())
    return done

def worker(domain: str, out_dir: Path, timeout: int, session: requests.Session, logf) -> Tuple[str,bool,str]:
    html_file=out_dir/f"{sanitize_filename(domain)}.html"
    err_file=out_dir/f"{sanitize_filename(domain)}.error"
    if html_file.exists() or err_file.exists():
        if logf: print(f"{domain},skip,already-have", file=logf, flush=True)
        return domain, True, "skip-existing"
    ok, note, html = fetch_homepage(domain, timeout=timeout, session=session)
    if ok and html:
        tmp=html_file.with_suffix(".html.part")
        with open(tmp,"wb") as fh: fh.write(html)
        os.replace(tmp, html_file)
        if logf: print(f"{domain},ok,{note}", file=logf, flush=True)
        return domain, True, f"ok ({note})"
    else:
        err_file.write_text(note or "fail\n", encoding="utf-8")
        if logf: print(f"{domain},fail,{note}", file=logf, flush=True)
        return domain, False, note or "fail"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("domains_file")
    ap.add_argument("out_dir")
    ap.add_argument("--workers", type=int, default=32)
    ap.add_argument("--timeout", type=int, default=5)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--log", type=str, default=None)
    ap.add_argument("--ipv4", action="store_true")
    ap.add_argument("--future-timeout", type=int, default=15,
        help="Max seconds to wait on any worker future (default: 15)")
    args=ap.parse_args()

    out_dir=Path(args.out_dir); out_dir.mkdir(parents=True,exist_ok=True)
    domains=read_domains(args.domains_file,args.limit)
    already=list_already_handled(out_dir)
    pending=[d for d in domains if sanitize_filename(d).lower() not in already]
    print(f"Total domains in file: {len(domains)}")
    print(f"Already handled: {len(already)}")
    print(f"Pending this run: {len(pending)}")
    if not pending: sys.exit(0)

    session=build_session(args.timeout,args.retries,args.ipv4)
    logf=open(args.log,"w",encoding="utf-8") if args.log else None
    if logf: print("domain,status,note",file=logf)

    ok=fail=0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures={ex.submit(worker,d,out_dir,args.timeout,session,logf): d for d in pending}
        for fut in tqdm(cf.as_completed(futures, timeout=args.future_timeout*len(futures)),
                        total=len(futures), desc="Downloading"):
            domain=futures[fut]
            try:
                _,s,_=fut.result(timeout=args.future_timeout)
                if s: ok+=1
                else: fail+=1
            except cf.TimeoutError:
                fail+=1
                err_file=out_dir/f"{sanitize_filename(domain)}.error"
                err_file.write_text("future-timeout\n",encoding="utf-8")
                if logf: print(f"{domain},fail,future-timeout",file=logf,flush=True)
            except Exception as e:
                fail+=1
                err_file=out_dir/f"{sanitize_filename(domain)}.error"
                err_file.write_text(f"future-exc:{e}\n",encoding="utf-8")
                if logf: print(f"{domain},fail,future-exc:{e}",file=logf,flush=True)

    if logf: logf.close()
    print(f"\nDone. Success:{ok}, Fail:{fail}")

if __name__=="__main__": main()
