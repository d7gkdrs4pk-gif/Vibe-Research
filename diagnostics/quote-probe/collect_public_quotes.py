#!/usr/bin/env python3
"""One-shot transport probe; NOT production or full-application acceptance.
Only public market requests. No accounts, holdings, credentials, model calls,
orders or schedules. Raw responses are data and are never executed.
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
TARGET = '2026-10-28'
BASE = 'db3a7c60afcb643abdf36933294765d1cbfc2b9c'
SYMBOLS = {
    'sz002085': '0.002085', 'sh510300': '1.510300',
    'sh000001': '1.000001', 'sz399001': '0.399001',
    'sz399006': '0.399006', 'sh000300': '1.000300',
}
OUT = Path('quote-probe-results')
MAX_BYTES = 512_000


def now() -> datetime:
    return datetime.now(TZ)


def collect(url: str, label: str) -> dict:
    row = {'label': label, 'url': url, 'requested_at': now().isoformat(),
           'http_ok': False, 'quote_accepted': False}
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Accept': '*/*',
        })
        with urllib.request.urlopen(req, timeout=8) as response:
            row['http_status'] = response.status
            row['response_url'] = response.url
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ValueError('Response exceeded bounded capture size')
        raw_path = OUT / 'raw' / (label + '.bin')
        raw_path.write_bytes(body)
        row.update(http_ok=row['http_status'] == 200,
                   raw_file='raw/' + label + '.bin', bytes=len(body),
                   sha256=hashlib.sha256(body).hexdigest())
        # A single base64 line cannot inject workflow commands or terminal escapes.
        print('RAW_BASE64 ' + label + ' ' + base64.b64encode(body).decode('ascii'), flush=True)
    except Exception as exc:
        row['error'] = type(exc).__name__ + ': ' + str(exc)[:600]
    row['completed_at'] = now().isoformat()
    return row


def main() -> int:
    OUT.mkdir(exist_ok=True)
    (OUT / 'raw').mkdir(exist_ok=True)
    started = now()
    report = {'target_date': TARGET, 'started_at': started.isoformat(),
              'reference_upstream_commit': BASE,
              'runner_commit': os.environ.get('GITHUB_SHA'),
              'production_approved': False, 'requests': [],
              'scope': 'Transport evidence only; source timestamps and prices require separate validation.'}
    if started.date().isoformat() != TARGET or (started.hour, started.minute) < (15, 5):
        report['blocked_reason'] = 'Wrong Beijing date or before 15:05; verify calendar before creating another dated probe.'
    else:
        url = 'https://qt.gtimg.cn/q=' + ','.join(SYMBOLS)
        report['requests'].append(collect(url, 'tencent_batch'))
        fields = 'f13,f43,f44,f45,f46,f47,f48,f57,f58,f60,f86,f168,f170'
        for symbol, secid in SYMBOLS.items():
            time.sleep(1.05)
            url = 'https://push2.eastmoney.com/api/qt/stock/get?fltt=2&invt=2&secid=' + secid + '&fields=' + fields
            report['requests'].append(collect(url, 'eastmoney_' + symbol))
    report['completed_at'] = now().isoformat()
    report['http_ok_count'] = sum(r['http_ok'] for r in report['requests'])
    report['requested_count'] = len(report['requests'])
    text = json.dumps(report, ensure_ascii=False, indent=2)
    (OUT / 'transport_report.json').write_text(text, encoding='utf-8')
    print('TRANSPORT_REPORT ' + json.dumps(report, ensure_ascii=True), flush=True)
    summary = '# Public quote transport probe\n\nNot production acceptance. No account data or trades.\n\n'
    summary += 'Target: ' + TARGET + '\n\n'
    summary += 'HTTP 200 responses: ' + str(report['http_ok_count']) + '/' + str(report['requested_count']) + '\n'
    summary += 'Production approved: **false**. Inspect raw source dates, security identity, units and provider conflicts separately.\n'
    if report.get('blocked_reason'):
        summary += '\nBlocked: ' + report['blocked_reason'] + '\n'
    (OUT / 'SUMMARY.md').write_text(summary, encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write(summary)
    return 0 if report['requested_count'] == 7 and report['http_ok_count'] == 7 else 2


if __name__ == '__main__':
    sys.exit(main())
