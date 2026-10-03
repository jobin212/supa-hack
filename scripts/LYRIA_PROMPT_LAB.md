# Local Lyria prompt experiments

Run selected prompts against the same Interactions endpoint and model used by
Threadsong. This script makes no Supabase, database, or Ando changes. Successful
API requests may incur charges. There are no automatic retries.

```sh
uv run python scripts/lyria_prompt_lab.py \
  --cases .data/lyria-prompt-lab/cases.json \
  --case simple_hyperpop --case clean_descriptive_style
```

The script reads `GEMINI_API_KEY` and `LYRIA_MODEL` from the application's settings,
including the gitignored `.env`. `--model` explicitly overrides the model.
Select up to six cases per batch, then inspect the results before running more.
Access, quota, and transport failures stop the batch. HTTP 400 content rejections
are recorded and the next explicitly selected case proceeds.

The cases file is a JSON object with named entries:

```json
{
  "simple_hyperpop": {
    "hypothesis": "A simple genre instruction",
    "prompt": "Create an original hyperpop song about a hopeful new beginning."
  }
}
```

Each run creates a timestamped directory under `.data/lyria-prompt-lab/` with
exact requests, responses, decoded audio, per-case summaries, and `results.json`.
Success responses reference saved audio files instead of duplicating base64 audio.
Authentication headers are not recorded and the key is redacted from text files.
All run artifacts stay gitignored. Request hashes identify identical prompts.

## Findings from 2026-10-03

Model: `lyria-3.5`. Eight local requests, no retries, three successful audio files,
five HTTP 400 `prohibited_content` responses. The title, saved lyrics, duration
instruction, and request structure stayed fixed; only the style field changed.

| Style | Result |
| --- | --- |
| High-energy glitchy hyperpop with pitched-up vocals and distorted synths | Blocked twice |
| Hyperpop | HTTP 200, valid MP3 |
| High-energy glitchy hyperpop with distorted synths | Blocked |
| High-energy glitchy hyperpop with pitched-up vocals | Blocked |
| Hyperpop with bright synthesizers, energetic electronic drums, and clear sung vocals | HTTP 200, valid MP3 |
| High-energy glitchy hyperpop | HTTP 200, valid MP3 |
| Hyperpop with pitched-up vocals and distorted synths | Blocked |

The `real_newlines` case was actually an identical baseline repeat: the saved
lyrics already contained real newline characters. Both baseline request hashes
are identical. JSON display escaping was initially mistaken for literal `\\n`;
the character-level check ruled that out before interpreting results.

Interpretation: the production-effect phrases each correlate with rejection in
this specific prompt context. The genre, original lyrics, and topic all passed
with other style descriptions. This is consistent with a moderation false
positive around those descriptions; Google does not reveal the exact trigger.
These are small-sample observations, not a universal blacklist or a guarantee
that an accepted prompt will always pass.

These initial experiments motivated simpler prompts. Later live requests also
blocked a plain synth-pop draft and a ballad, while a pitch-shifted-vocal hyperpop
draft succeeded. The style observations do not explain all failures. The application
now uses the plain-summary approach documented below, instead of prewriting lyrics.

All three successful MP3s validated with macOS `afinfo`: 44.1 kHz stereo, 192 kbps,
66.56–66.82 seconds. The request asked for about 30 seconds, so that instruction
did not impose an exact duration in these runs. Playback quality is for human review.

Local evidence:

- `.data/lyria-prompt-lab/20261003T213843.146243Z/results.json`
- `.data/lyria-prompt-lab/20261003T213950.597455Z/results.json`
- `.data/lyria-prompt-lab/20261003T214108.767656Z/results.json`

Playable samples:

- [Simple hyperpop](../.data/lyria-prompt-lab/20261003T213843.146243Z/simple_hyperpop/song-1.mp3)
- [Descriptive hyperpop](../.data/lyria-prompt-lab/20261003T213950.597455Z/clean_descriptive_style/song-1.mp3)
- [High-energy glitchy hyperpop](../.data/lyria-prompt-lab/20261003T214108.767656Z/genre_adjectives_only/song-1.mp3)

API reference: [Google's Lyria guide](https://ai.google.dev/gemini-api/docs/music-generation).

## Summary-first prototype

A subsequent local test used Gemini 3.8 Flash to summarize the sales-loss thread
into `theme`, `mood`, `genre`, and `key_phrases`, without writing lyrics or adding
production effects. Lyria 3.5 received that brief and wrote both lyrics and audio.
This preserves two model calls: Gemini for the brief, then Lyria for the song.

The brief described losing a sales deal while encouraging the team to persevere,
with a resilient, hopeful mood and the user-requested `hyperpop` genre. Lyria
returned HTTP 200 in 28.29 seconds, with a valid MP3. This is one successful trial,
not a measured improvement in overall acceptance or music quality.

- Brief and exact summarizer input: `.data/lyria-prompt-lab/summary-brief.json`
- Request and result: `.data/lyria-prompt-lab/20261003T214927.478363Z/summary_brief/`
- [Listen to the brief-based sample](../.data/lyria-prompt-lab/20261003T214927.478363Z/summary_brief/song-1.mp3)

Google's [prompt guide](https://ai.google.dev/gemini-api/docs/lyria-prompt-guide)
explicitly lists Hyperpop, gives distortion and pitch-shifted-vocal examples,
and supports either supplied lyrics or a narrative/mood brief for generated
lyrics. The rejected phrases above must not be treated as universally banned.
The [API error guide](https://ai.google.dev/gemini-api/docs/api-errors) defines
`prohibited_content`, but does not identify the triggering substring in our
responses. This structured prototype was followed by the simpler application
change below.

## Plain-summary application flow

Gemini now returns a display title, a one- or two-sentence summary of the people,
events, and intended mood, and a style only if the user explicitly requested one.
Lyria receives the summary, target duration, and optional style. It writes the
lyrics itself; the display title and raw thread are not sent to Lyria. Legacy
checkpointed lyric drafts remain readable for restart compatibility.

One live local test used the updated composer and fetched the sales-win thread
through the real Ando adapter. The thread had gained a subsequent message about
winning a sales competition. Gemini's summary retained Joseph, Bob, Sammie,
the sales quarter/competition, and the celebratory mood, with no invented style.
The request returned valid MP3 audio in 29.44 seconds: 66.35 seconds of audio,
44.1 kHz stereo, 192 kbps. No Ando message was posted by this test.

Exact summarizer input, brief, Lyria request, result, and audio are stored privately
under `.data/simple-summary-test/20261003T221112Z/`. This one successful test is
not evidence that all future content-filter blocks are resolved.
