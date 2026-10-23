"""Prepare local CC BY-SA frequency dictionaries without proprietary research data."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import urllib.request

SOURCE_COMMIT = '525f9b560de45753a5ea01069454e72e9aa541c6'
SOURCE_ROOT = f'https://raw.githubusercontent.com/hermitdave/FrequencyWords/{SOURCE_COMMIT}/content/2018'
PATTERNS = {'ru': re.compile(r'[а-яё]{2,40}\Z'), 'en': re.compile(r'[a-z]{2,40}\Z'),
            'he': re.compile(r'[א-ת]{2,40}\Z')}
LIMITS = {'ru':40000,'en':35000,'he':45000}
OUTPUTS = [f'{prefix}{language}.txt' for language in LIMITS for prefix in ('','confident_')]
OUTPUTS += ['he_custom.txt','exclude.txt','SOURCES.json','DATA-LICENSE.txt']


def select_words(raw, language):
    words = {}
    for line in raw.decode('utf-8-sig').splitlines():
        try:
            word, count = line.rsplit(maxsplit=1); count = int(count)
        except ValueError:
            continue
        word = unicodedata.normalize('NFKC',word).casefold()
        word = ''.join(c for c in word if not unicodedata.combining(c))
        if not PATTERNS[language].fullmatch(word): continue
        if language=='he' and any(c in 'ךםןףץ' for c in word[:-1]): continue
        minimum = 300 if len(word)==2 else 80 if len(word)==3 else 12
        if count >= minimum: words[word] = max(count,words.get(word,0))
    if len(words)<10000: raise ValueError(f'Unexpectedly small {language} dictionary: {len(words)}')
    return words


def prepare(output_dir):
    existing = [name for name in OUTPUTS if (output_dir/name).exists()]
    if existing:
        raise ValueError('Existing files will not be overwritten: '+', '.join(existing)+
                         '. Choose a new --output-dir or preserve and move the existing data first.')
    output = {}; manifest = {'license':'CC-BY-SA-4.0','attribution':'Hermit Dave / FrequencyWords; OpenSubtitles 2018',
                            'source_commit':SOURCE_COMMIT,'sources':{}}
    for language in LIMITS:
        url = f'{SOURCE_ROOT}/{language}/{language}_full.txt'
        print(f'Downloading {language} source...',flush=True)
        with urllib.request.urlopen(url,timeout=60) as stream: raw = stream.read()
        words = select_words(raw,language)
        confident = sorted(words,key=lambda word:(-words[word],word))[:LIMITS[language]]
        output[f'{language}.txt'] = '\n'.join(sorted(words))+'\n'
        output[f'confident_{language}.txt'] = '\n'.join(sorted(confident))+'\n'
        manifest['sources'][language] = {'url':url,'sha256':hashlib.sha256(raw).hexdigest(),
                                         'words':len(words),'confident':len(confident)}
        print(f'{language}: {len(words)} words, {len(confident)} confidence entries',flush=True)
    output['he_custom.txt'] = 'מחשב\n'
    output['exclude.txt'] = '# Optional local exclusions, one per line.\n'
    output['SOURCES.json'] = json.dumps(manifest,ensure_ascii=False,indent=2)+'\n'
    output['DATA-LICENSE.txt'] = ('Data: CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0/\n'
                                'Attribution: Hermit Dave / FrequencyWords; OpenSubtitles 2018 contributors.\n'
                                'Sources and hashes: SOURCES.json.\n'
                                'Modified: Unicode/script/length/frequency filtering; Hebrew final-form filtering; confidence ranking.\n')
    output_dir.mkdir(parents=True,exist_ok=True)
    # Exclusive creation also prevents overwriting a file created during download.
    for name, text in output.items():
        with (output_dir/name).open('x',encoding='utf-8',newline='\n') as stream: stream.write(text)
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,default=Path(__file__).resolve().parent/'data')
    args=parser.parse_args()
    try: prepare(args.output_dir)
    except (ValueError,OSError) as error: parser.exit(1,str(error)+'\n')


if __name__=='__main__': main()
