import argparse
import asyncio
import json
import time

import httpx
import uvicorn

from threadsong.config import Settings


async def demo(base: str):
    async with httpx.AsyncClient(base_url=base) as client:
        created = await client.post("/demo")
        created.raise_for_status()
        job = created.json()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = await client.get(f"/demo/jobs/{job['job_id']}")
            result.raise_for_status()
            body = result.json()
            if body["status"] in {"complete", "failed"}:
                print(json.dumps(body, indent=2))
                return
            await asyncio.sleep(0.25)
        raise SystemExit("Demo still processing; inspect its job endpoint.")


def main():
    parser = argparse.ArgumentParser(description="Threadsong on Supabase Compute")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int)
    show_demo = commands.add_parser("demo")
    show_demo.add_argument("--url", default="http://localhost:8080")
    args = parser.parse_args()
    if args.command == "serve":
        settings = Settings()
        # One ASGI process owns one worker. Durable leases allow a later multi-instance split.
        # Disable access logs: /s/<token> contains a bearer sharing capability.
        uvicorn.run(
            "threadsong.app:create_app",
            factory=True,
            host=args.host,
            port=args.port or settings.port,
            access_log=False,
        )
    elif args.command == "demo":
        asyncio.run(demo(args.url))
