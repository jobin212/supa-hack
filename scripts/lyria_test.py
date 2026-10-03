#!/usr/bin/env python3
"""Generate one Lyria clip; save the input, response and decoded audio.

Run: source ~/.zshrc && python3 scripts/lyria_test.py
Uses only the Python standard library. No credentials are written to disk.
Docs: https://ai.google.dev/gemini-api/docs/music-generation
"""

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROMPT = """Create a 30-second upbeat electronic indie-pop song about building
something new at a weekend hackathon. Warm synth chords, bouncy bass, crisp drums,
and a catchy, clear sung hook at around 115 BPM. Start immediately, build energy,
and finish with a clean musical ending. Use these original lyrics:
One idea and a midnight glow,
Little sparks begin to grow,
Build it, play it, make it shine,
Something new, one beat at a time.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='lyria-3-clip-preview')
    parser.add_argument('--prompt', default=PROMPT)
    args = parser.parse_args()
    # Follow Google's documented precedence if both are configured.
    key = os.environ.get('GOOGLE_API_KEY') or os.environ.get('GEMINI_API_KEY')
    if not key:
        sys.exit('No API key found. Load ~/.zshrc before running this script.')
    run = Path(__file__).resolve().parent / 'lyria-test-runs' / dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%S.%fZ')
    run.mkdir(parents=True, exist_ok=False)

    def save(name, value):
        text = json.dumps(value, indent=2, ensure_ascii=False) + '\n'
        (run / name).write_text(text.replace(key, '[REDACTED]'))

    endpoint = 'https://generativelanguage.googleapis.com/v1beta/interactions'
    payload = {'model': args.model, 'input': args.prompt}
    save('request.json', {'endpoint': endpoint, 'body': payload})
    (run / 'prompt.txt').write_text(args.prompt.replace(key, '[REDACTED]'))
    started = time.monotonic()
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers={
        'Content-Type': 'application/json', 'x-goog-api-key': key,
    })
    print(f'Generating with {args.model}; recording to {run}', flush=True)
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            result = json.load(response)
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read().decode(errors='replace').replace(key, '[REDACTED]')
        try:
            detail = json.loads(raw)
        except ValueError:
            detail = {'message': raw}
        save('error.json', {'http_status': error.code, 'response': detail})
        save('summary.json', {'success': False, 'elapsed_seconds': round(time.monotonic() - started, 2)})
        print(f'HTTP {error.code}: {json.dumps(detail)}')
        return 1
    except (urllib.error.URLError, TimeoutError) as error:
        save('error.json', {'message': str(error)})
        print(f'Request failed. See {run / "error.json"}')
        return 1

    save('response.json', result)
    audio_files, texts = [], []
    seen = set()

    def extract(value):
        if isinstance(value, list):
            for item in value:
                extract(item)
        elif isinstance(value, dict):
            if value.get('type') == 'text' and value.get('text'):
                texts.append(value['text'])
            if value.get('type') == 'audio' and value.get('data'):
                data = base64.b64decode(value['data'], validate=True)
                digest = hashlib.sha256(data).hexdigest()
                if digest not in seen:
                    seen.add(digest)
                    mime = value.get('mime_type', 'audio/mpeg')
                    suffix = '.wav' if 'wav' in mime else '.mp3'
                    name = f'song-{len(audio_files) + 1}{suffix}'
                    (run / name).write_bytes(data)
                    audio_files.append({'file': name, 'bytes': len(data), 'sha256': digest, 'mime_type': mime})
            for item in value.values():
                if isinstance(item, (dict, list)):
                    extract(item)

    extract(result)
    (run / 'output-text.txt').write_text('\n\n'.join(texts).replace(key, '[REDACTED]'))
    save('summary.json', {'success': bool(audio_files), 'http_status': status,
        'model': args.model, 'elapsed_seconds': round(time.monotonic() - started, 2),
        'audio_files': audio_files})
    print(f'Saved {len(audio_files)} audio file(s) to {run}')
    return 0 if audio_files else 1


if __name__ == '__main__':
    sys.exit(main())
