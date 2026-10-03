"""Run selected local Lyria prompt experiments; never deploy or post to Ando.

uv run python scripts/lyria_prompt_lab.py --cases .data/lyria-prompt-lab/cases.json \
    --case original --case real_newlines

Each selected case makes ONE API request, with no retries. Successful requests
may incur charges. Prompts, responses, audio and summaries remain in .data/.
Uses the same GEMINI_API_KEY and default model as the application.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from threadsong.config import Settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--case", action="append", required=True, dest="selected")
    parser.add_argument("--model", help="Defaults to LYRIA_MODEL from app settings")
    parser.add_argument("--output", type=Path, default=Path(".data/lyria-prompt-lab"))
    args = parser.parse_args()
    settings = Settings()
    key = settings.gemini_api_key.get_secret_value()
    if not key:
        parser.error("Set GEMINI_API_KEY in the environment or gitignored .env")
    cases = json.loads(args.cases.read_text())
    selected = list(dict.fromkeys(args.selected))
    if len(selected) > 6:
        parser.error("Select at most six cases per run; inspect results before continuing")
    for name in selected:
        if not re.fullmatch(r"[a-z0-9_-]+", name) or name not in cases:
            parser.error(f"Unknown or invalid case: {name}")
    model = args.model or settings.lyria_model
    endpoint = "https://generativelanguage.googleapis.com/v1beta/interactions"
    run = args.output / datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    run.mkdir(parents=True, mode=0o700)

    def save(path, value):
        encoded = json.dumps(value, indent=2, ensure_ascii=False).replace(key, "[REDACTED]")
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
            f.write(encoded + "\n")

    def extract(value, directory, audio_files):
        if isinstance(value, list):
            return [extract(item, directory, audio_files) for item in value]
        if isinstance(value, dict):
            if value.get("type") == "audio" and value.get("data"):
                raw = base64.b64decode(value["data"], validate=True)
                mime = value.get("mime_type", "audio/mpeg")
                suffix = "wav" if "wav" in mime else "mp3"
                name = f"song-{len(audio_files) + 1}.{suffix}"
                with os.fdopen(
                    os.open(directory / name, os.O_WRONLY | os.O_CREAT, 0o600), "wb"
                ) as f:
                    f.write(raw)
                metadata = {
                    "file": name,
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "mime_type": mime,
                }
                audio_files.append(metadata)
                return {**{k: v for k, v in value.items() if k != "data"}, "saved_audio": metadata}
            return {k: extract(v, directory, audio_files) for k, v in value.items()}
        return value

    results = []
    print(f"Run: {run}\nModel: {model}; requests: {len(selected)}; retries: 0", flush=True)
    with httpx.Client(timeout=300, follow_redirects=False) as client:
        for name in selected:
            case = cases[name]
            prompt = case["prompt"]
            directory = run / name
            directory.mkdir(mode=0o700)
            payload = {"model": model, "input": prompt}
            save(directory / "request.json", {"endpoint": endpoint, "body": payload})
            save(directory / "case.json", case)
            started = time.monotonic()
            summary = {
                "case": name,
                "hypothesis": case.get("hypothesis"),
                "model": model,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "success": False,
            }
            try:
                response = client.post(endpoint, headers={"x-goog-api-key": key}, json=payload)
                summary["http_status"] = response.status_code
                try:
                    body = response.json()
                except ValueError:
                    body = {"non_json_response": response.text[:4000]}
                audio_files = []
                safe_body = extract(body, directory, audio_files)
                save(directory / "response.json", safe_body)
                summary["audio_files"] = audio_files
                summary["success"] = response.is_success and bool(audio_files)
                if isinstance(body, dict) and isinstance(body.get("error"), dict):
                    error = body["error"]
                    summary["provider_code"] = error.get("code", error.get("status"))
                    summary["error_message"] = error.get("message", "")[:1000]
            except (httpx.HTTPError, ValueError) as exc:
                summary["error_type"] = type(exc).__name__
            summary["elapsed_seconds"] = round(time.monotonic() - started, 2)
            save(directory / "summary.json", summary)
            results.append(summary)
            save(run / "results.json", results)
            print(json.dumps(summary, ensure_ascii=False).replace(key, "[REDACTED]"), flush=True)
            if summary.get("http_status") in (401, 403, 429) or summary.get("error_type"):
                print("Stopping this batch after an access/quota/transport error.", flush=True)
                break
    print(f"Results: {run / 'results.json'}", flush=True)


if __name__ == "__main__":
    main()
