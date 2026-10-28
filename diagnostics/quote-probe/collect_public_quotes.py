#!/usr/bin/env python3
"""One-shot independent-source probe, not production acceptance.
Uses the unchanged upstream TDX package parser; Sina is a bounded fallback.
Only public GET requests, no account data, credentials, trades or scheduling.
Tencent evidence is reused offline from run 40654539086, not fetched again.
"""
from __future__ import annotations
import ast
import base64
import hashlib
import io
import json
import math
import os
import re
import struct
import sys
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
TARGET = '2026-10-28'
BASE = 'db3a7c60afcb643abdf36933294765d1cbfc2b9c'
PARSER_PATH = '.agents/skills/data-access/scripts/sources/astock_ported.py'
PARSER_BLOB = 'f12c82cf1af9d05a93014882394786767b42a5f0'
SYMBOLS = ['sz002085', 'sh510300', 'sh000001', 'sz399001', 'sz399006', 'sh000300']
TDX_MAP = {s: ('sh999999' if s == 'sh000001' else s) for s in SYMBOLS}
OUT = Path('quote-probe-results')


def now():
    return datetime.now(TZ)


def collect(url, label, limit, referer=None):
    meta = {'label': label, 'url': url, 'requested_at': now().isoformat(),
            'http_ok': False, 'quote_accepted': False}
    body = None
    try:
        headers = {'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'}
        if referer:
            headers['Referer'] = referer
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=25) as r:
            meta.update(http_status=r.status, response_url=r.url,
                        content_type=r.headers.get('Content-Type'), last_modified=r.headers.get('Last-Modified'))
            payload = r.read(limit + 1)
        if len(payload) > limit:
            raise ValueError('Response exceeds bounded capture size')
        path = OUT / 'raw' / (label + '.bin')
        path.write_bytes(payload)
        meta.update(http_ok=meta['http_status'] == 200, raw_file=str(path.relative_to(OUT)),
                    bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        body = payload
        if len(payload) <= 16_000:
            print('RAW_BASE64 ' + label + ' ' + base64.b64encode(payload).decode('ascii'), flush=True)
    except urllib.error.HTTPError as e:
        meta.update(http_status=e.code, error='HTTPError: ' + str(e)[:400])
        try:
            error_body = e.read(16_000)
            error_path = OUT / 'raw' / (label + '.error.bin')
            error_path.write_bytes(error_body)
            meta.update(error_raw_file=str(error_path.relative_to(OUT)),
                        error_sha256=hashlib.sha256(error_body).hexdigest())
        except Exception:
            pass
    except Exception as e:
        meta['error'] = type(e).__name__ + ': ' + str(e)[:400]
    meta['completed_at'] = now().isoformat()
    return body, meta


def parse_tdx(payload):
    """Execute only the reviewed parser function from the pinned upstream blob."""
    src = Path(PARSER_PATH).read_bytes()
    blob = hashlib.sha1(b'blob ' + str(len(src)).encode() + b'\0' + src).hexdigest()
    if blob != PARSER_BLOB:
        raise ValueError('Upstream parser changed; review required before execution')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        infos = archive.infolist()
        if len(infos) > 50 or sum(i.file_size for i in infos) > 100_000_000:
            raise ValueError('Unexpected archive size; refusing decompression')
        if len({i.filename for i in infos}) != len(infos):
            raise ValueError('Duplicate archive member names')
        members = [i.filename for i in infos]
    # No module-level code is executed; this function uses only the supplied standard library globals.
    tree = ast.parse(src.decode('utf-8'))
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_tdx_parse_package']
    if len(funcs) != 1 or funcs[0].decorator_list:
        raise ValueError('Unexpected upstream parser definition')
    env = {'io': io, 'math': math, 're': re, 'struct': struct, 'zipfile': zipfile,
           'TDX_BJ_FIRST_DAY': '20220506', 'TDX_MIN_PRICED': {'sh': 10000, 'sz': 3000, 'bj': 50}}
    exec(compile(ast.Module(body=funcs, type_ignores=[]), PARSER_PATH, 'exec'), env)
    rows = env['_tdx_parse_package'](payload, TARGET.replace('-', ''))
    keyed = {r['market'] + r['code']: r for r in rows}
    selected = {s: keyed.get(mapped) for s, mapped in TDX_MAP.items()}
    return {'parser_blob': blob, 'archive_members': members, 'total_priced_rows': len(rows),
            'selected': selected, 'mapping': TDX_MAP,
            'date_basis': 'Requested URL plus exact YYMMDD of all internal market file pairs; no invented source clock',
            'production_approved': False}


def main():
    (OUT / 'raw').mkdir(parents=True, exist_ok=True)
    started = now()
    report = {'target_date': TARGET, 'started_at': started.isoformat(),
              'reference_upstream_commit': BASE, 'runner_commit': os.environ.get('GITHUB_SHA'),
              'baseline_run_id': 40654539086, 'production_approved': False, 'requests': [],
              'scope': 'Independent second-provider evidence only; reuse archived Tencent baseline offline.'}
    success = False
    if started.date().isoformat() != TARGET or (started.hour, started.minute) < (15, 5):
        report['blocked_reason'] = 'Wrong Beijing date or before 15:05; do not reuse stale dated probe.'
    else:
        url = 'https://www.tdx.com.cn/products/data/data/g4day/' + TARGET.replace('-', '') + '.zip'
        body, meta = collect(url, 'tdx_daily_package', 12_000_000)
        report['requests'].append(meta)
        if body is not None and meta['http_ok']:
            try:
                if not body.startswith(b'PK'):
                    raise ValueError('Not a ZIP response')
                result = parse_tdx(body)
                (OUT / 'tdx_selected.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
                report['tdx_parse'] = result
                success = all(result['selected'].get(s) for s in SYMBOLS)
            except Exception as e:
                report['tdx_parse_error'] = type(e).__name__ + ': ' + str(e)[:600]
        if not success:
            report['fallback_reason'] = 'TDX did not yield all six target-date records; one Sina batch request, no retries.'
            body, meta = collect('https://hq.sinajs.cn/list=' + ','.join(SYMBOLS),
                                 'sina_batch', 512_000, 'https://finance.sina.com.cn/')
            report['requests'].append(meta)
            # HTTP success is transport only. Identity/date/final-price validation happens separately.
            success = body is not None and meta['http_ok']
    report['completed_at'] = now().isoformat()
    report['http_ok_count'] = sum(r['http_ok'] for r in report['requests'])
    (OUT / 'transport_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('TRANSPORT_REPORT ' + json.dumps(report, ensure_ascii=True), flush=True)
    summary = '# Independent quote provider probe\n\nTarget: ' + TARGET + '\n\n'
    summary += 'Production approved: **false**. Raw evidence requires date, identity, unit and cross-provider checks.\n'
    summary += 'HTTP 200: ' + str(report['http_ok_count']) + '/' + str(len(report['requests'])) + '\n'
    (OUT / 'SUMMARY.md').write_text(summary, encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write(summary)
    return 0 if success else 2


if __name__ == '__main__':
    sys.exit(main())
