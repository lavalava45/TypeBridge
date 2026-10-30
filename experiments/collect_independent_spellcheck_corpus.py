"""Collect independent encyclopedic titles from the public Wikimedia API.

No TypeBridge word lists, Windows spelling answers, personal data, keyboard
hooks or active user settings influence selection. Freeze the resulting snapshot
once and evaluate it separately. Internet is used ONLY by this collector.

The random-page API does not uniformly sample lexical words or proper names.
Each single-token title proves only that an encyclopedia article exists, NOT
that the token is spelled correctly in every context or is a common word.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'MediaWiki Action API, Wikipedia namespace 0, list=random'
API = 'https://{code}.wikipedia.org/w/api.php'
SCRIPTS = {'ru': 'CYRILLIC', 'en': 'LATIN', 'he': 'HEBREW'}
USER_AGENT = 'TypeBridge-Research/0.2 (independent offline spellcheck study)'


def valid_title(token: str, language: str) -> bool:
    """Only alphabetic one-token article titles from the expected script."""
    if not 4 <= len(token) <= 18 or not token.isalpha():
        return False
    return all(unicodedata.name(ch, '').startswith(SCRIPTS[language] + ' ')
               for ch in token)


def fetch(lang: str, amount: int, *, attempts: int = 5):
    params = {'action': 'query', 'list': 'random', 'rnnamespace': 0,
              'rnfilterredir': 'nonredirects', 'rnlimit': amount,
              'format': 'json', 'formatversion': 2}
    url = API.format(code=lang) + '?' + urllib.parse.urlencode(params)
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(request, timeout=25) as response:
                if response.status != 200 or 'json' not in response.headers.get('Content-Type', ''):
                    raise RuntimeError('Unexpected Wikimedia API response')
                data = json.load(response)
            if 'error' in data:
                raise RuntimeError(str(data['error']))
            return data['query']['random']
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as error:
            if attempt + 1 == attempts:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError('No result from API')


def collect(*, batch_count=5, batch_size=350, limit=160,
            previous=None, checkpoint=None):
    if not 1 <= batch_count <= 10 or not 1 <= batch_size <= 500 or not 20 <= limit <= 300:
        raise ValueError('Invalid sampling bounds')
    snapshot = previous if previous is not None else {
        'kind': 'PUBLIC_WIKIPEDIA_RANDOM_TITLE_SNAPSHOT',
        'version': 1,
        'retrieved_utc': datetime.now(timezone.utc).isoformat(),
        'provenance': SOURCE,
        'query': 'action=query&list=random&rnnamespace=0&rnfilterredir=nonredirects',
        'collection_strategy': ('random API starting point, fixed contiguous batches; '
                                'one-token alphabetic article titles, single script; '
                                'NO filtering by TypeBridge/Windows, NO lexical truth claim'),
        'license_notes': ('Wikipedia article titles and metadata; source links provided. '
                          'Wikipedia article content generally CC BY-SA 4.0; '
                          'see https://www.mediawiki.org/wiki/Wikimedia_APIs/Content_reuse'),
        'languages': {},
    }
    for lang in ('ru', 'en', 'he'):
        if lang in snapshot['languages']:
            print(f'{lang}: existing checkpoint retained', flush=True)
            continue
        recorded, seen = [], set()
        stats = {'batches': 0, 'pages_seen': 0, 'single_script_titles_seen': 0}
        for _ in range(batch_count):
            batch = fetch(lang, batch_size)
            stats['batches'] += 1
            stats['pages_seen'] += len(batch)
            for item in batch:
                title = item['title']
                if not valid_title(title, lang):
                    continue
                stats['single_script_titles_seen'] += 1
                ident = int(item['id'])
                if ident in seen or not ident:
                    continue
                seen.add(ident)
                recorded.append({'title': title, 'pageid': ident,
                                 'source_url': f'https://{lang}.wikipedia.org/?curid={ident}'})
                if len(recorded) >= limit:
                    break
            if len(recorded) >= limit:
                break
            time.sleep(1.0)
        snapshot['languages'][lang] = {'stats': stats, 'titles': recorded[:limit]}
        print(f'{lang}: {len(recorded)} single-token titles, scanned {stats["pages_seen"]} pages',
              flush=True)
        if checkpoint is not None:
            checkpoint.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n',
                                  encoding='utf-8')
    return snapshot


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch-count', type=int, default=5)
    parser.add_argument('--batch-size', type=int, default=350)
    parser.add_argument('--limit', type=int, default=160)
    parser.add_argument('--output', type=Path, default=ROOT / 'experiments' / 'fixtures' /
                        'windows_spellcheck_wikipedia_titles_20261030.json')
    args = parser.parse_args()
    path = args.output.resolve()
    if path.parent != ROOT / 'experiments' / 'fixtures' or path.exists():
        parser.error('Must create NEW non-existing file under experiments/fixtures')
    path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = path.with_suffix('.partial.json')
    previous = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else None
    if previous is not None and (previous.get('kind') !=
            'PUBLIC_WIKIPEDIA_RANDOM_TITLE_SNAPSHOT'
            or set(previous.get('languages', {})) - {'ru', 'en', 'he'}):
        parser.error('Existing checkpoint is not a valid Wikimedia title snapshot')
    result = collect(batch_count=args.batch_count, batch_size=args.batch_size,
                     limit=args.limit, previous=previous, checkpoint=checkpoint)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8')
    checkpoint.unlink(missing_ok=True)
    print(f'CORPUS={path}', flush=True)
