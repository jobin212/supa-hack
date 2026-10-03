# Lyria smoke test

This standalone Python script makes one Gemini API request to
`lyria-3-clip-preview`. It needs no extra packages or project changes.

From the repository root, in an interactive zsh terminal:

```zsh
source ~/.zshrc
export GEMINI_API_KEY
python3 scripts/lyria_test.py
```

The explicit export is necessary if the assignment in `.zshrc` does not use
`export`. Google documents that `GOOGLE_API_KEY` takes precedence if both keys
are set. This script follows that convention. Each rerun makes a new API request
and may incur usage charges.

Optional custom prompt:

```zsh
python3 scripts/lyria_test.py --prompt 'A short, warm acoustic folk song about a fresh start.'
```

Each run creates a unique UTC-named folder under `scripts/lyria-test-runs/`:

- `prompt.txt`: exact prompt.
- `request.json`: endpoint and request body, without authentication headers.
- `response.json`: full JSON response including base64 audio.
- `output-text.txt`: generated lyrics and other text.
- `song-1.mp3`: decoded audio.
- `summary.json`: model, status, elapsed time, audio size and SHA-256.
- `error.json`: API failure details, if the request fails.

Generated files are ignored by Git using a local `.gitignore` in the run folder's
parent. API keys are never intentionally logged or saved.

Verified run: `20261003T200239.004873Z`. HTTP 200 in 8.24 seconds;
MP3 validated with macOS `afinfo`: 30.77 seconds, 44.1 kHz stereo, 192 kbps.
The prompt requested an upbeat electronic indie-pop hackathon song with original
lyrics; the API returned audio and timestamped lyrics. Audio quality is for human
review.

Reference: https://ai.google.dev/gemini-api/docs/music-generation
