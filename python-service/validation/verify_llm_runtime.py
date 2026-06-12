#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import call_local_llm


def _print_section(title: str) -> None:
    print("\n" + "=" * 20 + f" {title} " + "=" * 20)


def _as_json(data: Any) -> str:
    try:
        return json.dumps(data, indent=2, ensure_ascii=True)
    except Exception:
        return str(data)


def check_tags(base_url: str) -> tuple[bool, dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        with httpx.Client(timeout=20.0) as client:
            res = client.get(url)
            res.raise_for_status()
            payload = res.json()
            return True, payload if isinstance(payload, dict) else {"raw": payload}
    except Exception as exc:
        return False, {"error": str(exc)}


def check_direct_chat(base_url: str, model: str, prompt: str) -> tuple[bool, dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/chat"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    try:
        with httpx.Client(timeout=90.0) as client:
            res = client.post(url, json=body)
            payload = res.json()
            if res.status_code >= 400:
                return False, payload if isinstance(payload, dict) else {"raw": payload}
            if not isinstance(payload, dict):
                return False, {"raw": payload}
            message = payload.get("message")
            content = ""
            if isinstance(message, dict):
                content = str(message.get("content") or "").strip()
            ok = bool(content)
            return ok, payload
    except Exception as exc:
        return False, {"error": str(exc)}


def check_app_llm_client(model: str) -> tuple[bool, dict[str, Any]]:
    try:
        raw = call_local_llm(
            "Reply with exactly APP_CLIENT_OK",
            system_prompt="You are a precise assistant. Output only the requested token.",
            model_override=model,
            temperature=0.0,
            max_tokens=40,
            timeout_sec=45.0,
        )
        content = str(raw or "").strip()
        ok = bool(content) and "APP_CLIENT_OK" in content
        return ok, {"raw": content[:400]}
    except Exception as exc:
        return False, {"error": str(exc)}


def main() -> int:
    provider = os.getenv("LOCAL_LLM_PROVIDER", "none").strip().lower()
    base_url = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434").strip()
    model = os.getenv("LOCAL_LLM_MODEL", "gamma13b").strip()
    prompt = "Think for a few seconds and answer exactly: VERIFIED_OK"

    print("LLM Runtime Verification")
    print(f"provider={provider}")
    print(f"base_url={base_url}")
    print(f"model={model}")

    _print_section("Tags")
    tags_ok, tags_payload = check_tags(base_url)
    print(f"ok={tags_ok}")
    model_names: list[str] = []
    if isinstance(tags_payload, dict):
        models = tags_payload.get("models")
        if isinstance(models, list):
            for item in models:
                if isinstance(item, dict):
                    model_names.append(str(item.get("name") or ""))
    print("models=", [name for name in model_names if name])
    if not tags_ok:
        print(_as_json(tags_payload))

    _print_section("Direct Chat")
    direct_ok, direct_payload = check_direct_chat(base_url, model, prompt)
    print(f"ok={direct_ok}")
    print(_as_json(direct_payload))

    _print_section("App LLM Client Path")
    app_ok, app_payload = check_app_llm_client(model)
    print(f"ok={app_ok}")
    print(_as_json(app_payload))

    overall_ok = tags_ok and direct_ok and app_ok
    _print_section("Result")
    print("PASS" if overall_ok else "FAIL")
    if not overall_ok:
        print("Reason: LLM is not fully answering through both direct and app paths.")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
