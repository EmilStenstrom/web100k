# web100k – 100,000 Real‑World Homepage HTML Snapshots

A compact, no‑frills dataset of 100,000 real homepage HTML documents from popular domains. It’s meant for benchmarking / fuzzing / robustness testing of HTML parsers, link extractors, readability algorithms, ML preprocessing pipelines, etc.

## Getting the Data
The HTML files are published as GitHub Release assets in 10 zstd‑compressed tar batches:

Files (latest release links):

1. [web100k-batch-001.tar.zst](https://github.com/EmilStenstrom/web100k/releases/latest/download/web100k-batch-001.tar.zst)
2. [web100k-batch-002.tar.zst](https://github.com/EmilStenstrom/web100k/releases/latest/download/web100k-batch-002.tar.zst)
3. [web100k-batch-003.tar.zst](https://github.com/EmilStenstrom/web100k/releases/latest/download/web100k-batch-003.tar.zst)
4. [web100k-batch-004.tar.zst](https://github.com/EmilStenstrom/web100k/releases/latest/download/web100k-batch-004.tar.zst)
5. [web100k-batch-005.tar.zst](https://github.com/EmilStenstrom/web100k/releases/latest/download/web100k-batch-005.tar.zst)
6. [web100k-batch-006.tar.zst](https://github.com/EmilStenstrom/web100k/releases/latest/download/web100k-batch-006.tar.zst)
7. [web100k-batch-007.tar.zst](https://github.com/EmilStenstrom/web100k/releases/latest/download/web100k-batch-007.tar.zst)
8. [web100k-batch-008.tar.zst](https://github.com/EmilStenstrom/web100k/releases/latest/download/web100k-batch-008.tar.zst)
9. [web100k-batch-009.tar.zst](https://github.com/EmilStenstrom/web100k/releases/latest/download/web100k-batch-009.tar.zst)
10. [web100k-batch-010.tar.zst](https://github.com/EmilStenstrom/web100k/releases/latest/download/web100k-batch-010.tar.zst)

You can also browse all assets: https://github.com/EmilStenstrom/web100k/releases

Approx total compressed size: ~3.1 GB.

Download (examples):
```bash
# Using GitHub CLI (recommended)
gh release download -R EmilStenstrom/web100k main --pattern 'web100k-batch-*.tar.zst'

# Or with curl (one file shown)
curl -L -o web100k-batch-001.tar.zst \
    https://github.com/EmilStenstrom/web100k/releases/download/main/web100k-batch-001.tar.zst
```

Extract all batches (creates a `downloaded/` directory with per‑domain `.html.zst` files):
```bash
mkdir -p downloaded
for f in web100k-batch-*.tar.zst; do
    echo "Extracting $f" >&2
    zstd -d -c "$f" | tar -x -C downloaded
done
```

Each extracted file: `<sanitized-domain>.html.zst`.

`html.dict` (zstd dictionary) is versioned in the repo (or release) and is required for optimal decompression.

## What’s Inside
 - Files: `<sanitized-domain>.html.zst` (zstd, shared dictionary `html.dict`)
 - Origin: Raw first response that *looked like* HTML for one of: `https://www.<d>/`, `https://<d>/`, `http://www.<d>/`, `http://<d>/`
 - No filtering / dedup / boilerplate removal. Content may include tracking scripts, cookies banners, adult material, gambling, geo‑specific text, etc.

## Filename Sanitizing
Non‑alphanumeric characters (except `.` and `-`) are replaced with `_` (see `sanitize_filename` in `crawl.py`). Example:
```
example.com        -> example.com.html.zst
foo+bar.co.uk      -> foo_bar.co.uk.html.zst
```

## Decompressing Individual Pages
You need both the page file and the `html.dict` dictionary.

Single file:
```bash
zstd -D html.dict -d downloaded/example.com.html.zst -o example.com.html
```
All files (outputs alongside originals):
```bash
find downloaded -name '*.html.zst' -print -exec bash -c 'zstd -q -D html.dict -d "$1" -o "${1%.zst}"' _ {} \;
```
Python (optional):
```python
import zstandard as zstd
import pathlib
D = zstd.ZstdDecompressor(dict_data=zstd.ZstdCompressionDict(open('html.dict','rb').read()))
for p in pathlib.Path('downloaded').glob('*.html.zst'):
    out = p.with_suffix('')
    with open(p,'rb') as f, open(out,'wb') as w:
        w.write(D.decompress(f.read()))
```

## Streaming / In‑Memory Usage (Preferred)
Often you just want to parse or inspect a page without creating a temporary uncompressed `.html` file. You can stream‑decompress directly from the compressed file using either the CLI or Python. This saves disk space and I/O.

CLI (pipe to another tool):
```bash
zstd -D html.dict -d -c downloaded/example.com.html.zst | head
```

Python (file streaming):
```python
import zstandard as zstd, io, pathlib

dict_bytes = open('html.dict','rb').read()
dctx = zstd.ZstdDecompressor(dict_data=zstd.ZstdCompressionDict(dict_bytes))

# Iterate over compressed HTML files (no on-disk extraction)
for idx, path in enumerate(sorted(pathlib.Path('downloaded').glob('*.html.zst'))):
    # Optional: limit number of files for a quick sample
    if idx >= 5:  # remove or adjust this guard to process all files
        break
    with open(path, 'rb') as fh, dctx.stream_reader(fh) as reader:
        text = io.TextIOWrapper(reader, encoding='utf-8', errors='replace')
        print(f"===== BEGIN {path.name} ({idx}) =====")
        for chunk in text:  # stream page content
            print(chunk, end='')
        print(f"\n===== END {path.name} =====\n")
```

Integrate this pattern into your parser pipeline to avoid materializing thousands of HTML files on disk.

## Reproducing the Crawl
Use `crawl.py` (it saves uncompressed `.html` / `.error` files; compression was a separate post‑step):
```bash
pip install -r requirements.txt
python crawl.py domains.txt downloaded --workers 32 --timeout 5 --retries 3 --log run.csv
```
You need a domain list: grab a fresh popular‑domains snapshot from the Tranco project (https://tranco-list.eu/) and save it as `domains.txt` (or pass its path). The dataset here used one such snapshot without extra curation.
Key behaviors (see docstring + code):
- Tries 4 URL variants per domain until one returns plausible HTML
- Auto‑decodes gzip/deflate/brotli (with safe fallbacks)
- Skips domains already having `.html` or `.error`
- Writes `<domain>.error` with a short failure tag
- Parallel via `ThreadPoolExecutor` with progress bar

## Intended Uses
- Parser correctness tests (invalid tag soup, encodings, script/style noise)
- Benchmarking tokenizers or boilerplate cleaners
- Fuzz inputs for security / robustness checks
- HTML → text / readability evaluations

## Caveats
- Raw, potentially unsafe or offensive content; treat accordingly
- Not a clean corpus for linguistic modeling without preprocessing
- Some pages may be partial, interstitial, consent walls, or error bodies that looked HTML‑ish
- Timestamps reflect crawl time only implicitly (not stored)

## Attribution / Licensing
No explicit license is bundled. If you plan redistribution, ensure compliance with the underlying sites’ terms. Consider adding a LICENSE file for clarity.

## Quick Checklist
- Need the data? Download release batches + extract + keep `html.dict`.
- Need plaintext HTML? Decompress with zstd + dictionary.
- Need to regenerate? Get a Tranco list, run `crawl.py`, then (optionally) batch‑compress.

Questions or ideas for small improvements (extra metadata, language stats, WARC export)? Open an issue / start a thread.

Enjoy exploring the messy real web!
