"""Read-only check of customer entrypoints, declared assets, and sample links."""
import concurrent.futures
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.request import urlopen

BASE = 'http://127.0.0.1:18080'
ROOT = Path(__file__).resolve().parents[1]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('a', 'link') and attrs.get('href'):
            self.urls.append(attrs['href'])
        if tag in ('script', 'img', 'iframe') and attrs.get('src'):
            self.urls.append(attrs['src'])


def fetch(url):
    try:
        with urlopen(url, timeout=20) as response:
            body = response.read()
            return {'url': url, 'status': response.status, 'bytes': len(body)}, body, response.headers.get('Content-Type', '')
    except Exception as error:
        return {'url': url, 'error': str(error)}, b'', ''


def main():
    with urlopen(BASE + '/api/status') as response:
        state = json.load(response)
    pending = {BASE + '/', BASE + '/api/materials', BASE + '/api/status'}
    pending.update(s['url'] for s in state['services'])
    # The PDF entrypoint bootstraps a demo session then navigates to these pages.
    pending.update(['http://127.0.0.1:18082/', 'http://127.0.0.1:18084/'])
    with urlopen(BASE + '/api/materials') as response:
        pending.update(BASE + '/materials/' + m['path'] for m in json.load(response))
    visited, external, results = set(), set(), []
    while pending:
        batch = sorted(pending - visited)
        pending.clear()
        if not batch:
            break
        visited.update(batch)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for record, body, kind in pool.map(fetch, batch):
                results.append(record)
                links = []
                if 'html' in kind:
                    parser = Links()
                    parser.feed(body.decode('utf-8', errors='replace'))
                    links = parser.urls
                elif 'javascript' in kind and '/static/js/' in record['url']:
                    links = re.findall(r"(?:from\s*|import\s*)['\"]([./][^'\"]+)['\"]", body.decode('utf-8', errors='replace'))
                for link in links:
                    if link.startswith(('#', 'data:', 'blob:', 'mailto:', 'javascript:')):
                        continue
                    url = urldefrag(urljoin(record['url'], link))[0]
                    if urlparse(url).hostname in ('127.0.0.1', 'localhost'):
                        if url not in visited:
                            pending.add(url)
                    else:
                        external.add(url)
    failed = [r for r in results if r.get('status') != 200]
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'checked': len(results), 'failed': failed, 'external_references': sorted(external), 'results': results}
    target = ROOT / 'runtime/link-audit.json'
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({k: report[k] for k in ('checked', 'failed', 'external_references')}, ensure_ascii=False))
    raise SystemExit(bool(failed))


if __name__ == '__main__':
    main()
