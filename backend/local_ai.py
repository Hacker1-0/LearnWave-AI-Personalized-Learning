"""Offline text generation through a loopback-only Ollama service.

The model runs on the app server, never inside the learner's phone/browser.
"""
from __future__ import annotations

import json
import os
import time
from threading import BoundedSemaphore
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

LOCAL_URL = "http://127.0.0.1:11434"
_MODEL_SLOT = BoundedSemaphore(1)


class LocalAIError(RuntimeError):
    """A safe, user-readable local inference error."""


def configured_local_model() -> str:
    return os.getenv("OLLAMA_MODEL", "qwen3:4b-instruct").strip() or "qwen3:4b-instruct"


def local_models() -> tuple[str, ...]:
    """Probe the local server quickly, without consulting an internet proxy."""
    try:
        with build_opener(ProxyHandler({})).open(LOCAL_URL + "/api/tags", timeout=1) as reply:
            data = json.load(reply)
        return tuple(item["name"] for item in data.get("models", []) if "name" in item)
    except (OSError, ValueError, URLError):
        return ()


def generate_local(prompt: str, on_text: Callable[[str], None] | None = None,
                   on_status: Callable[[str], None] | None = None) -> dict:
    model = configured_local_model()
    if model not in local_models():
        raise LocalAIError("Offline AI is not ready. Start LearnWave with Start-LearnWave.ps1 after the one-time setup.")
    if on_status:
        on_status("Starting offline AI on the server… The first answer may need time to load the model.")
    # Keep a shared demo laptop from loading several simultaneous GPU jobs.
    if not _MODEL_SLOT.acquire(timeout=5):
        raise LocalAIError("Offline AI is answering another learner. Please retry shortly; your previous answer is kept.")
    try:
        payload = {
            "model": model, "prompt": prompt, "stream": True, "think": False,
            "keep_alive": "30m",
            "options": {"num_ctx": 4096, "num_predict": 1200, "temperature": 0.4},
        }
        if model == "qwen3:4b-instruct":
            # Use the instruct model's direct-answer chat prefix. Some packaged
            # Qwen templates still unconditionally open a reasoning block.
            subject = prompt.replace("<|", "< |")
            payload["prompt"] = (
                "<|im_start|>user\n" + subject + "<|im_end|>\n"
                "<|im_start|>assistant\n"
            )
            payload["raw"] = True
        body = json.dumps(payload).encode("utf-8")
        request = Request(LOCAL_URL + "/api/generate", data=body,
                          headers={"Content-Type": "application/json"})
        chunks: list[str] = []
        finished = False
        started = time.monotonic()
        last_draw = started
        with build_opener(ProxyHandler({})).open(request, timeout=60) as reply:
            for line in reply:
                if time.monotonic() - started > 120:
                    raise LocalAIError("Offline AI took too long. Try a shorter topic or close other GPU-heavy applications.")
                if not line.strip():
                    continue
                packet = json.loads(line)
                if packet.get("error"):
                    raise LocalAIError("The local model could not finish. Restart local AI and retry.")
                if packet.get("response"):
                    chunks.append(packet["response"])
                    # Batch DOM updates: token-by-token rerendering can stutter on phones.
                    if on_text and time.monotonic() - last_draw >= 0.3:
                        on_text("".join(chunks))
                        last_draw = time.monotonic()
                if packet.get("done"):
                    if packet.get("done_reason") == "length":
                        raise LocalAIError("The answer reached its length limit. Try a narrower topic for a complete answer.")
                    finished = True
                    break
        text = "".join(chunks).strip()
        if not finished or not text:
            raise LocalAIError("Offline AI did not finish an answer. Please retry.")
        if on_text:
            on_text(text)
        return {"text": text, "source": "local", "model": model, "prompt": prompt}
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise LocalAIError("Could not reach offline AI. Start the local AI server and try again.") from exc
    finally:
        _MODEL_SLOT.release()
