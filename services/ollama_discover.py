"""Find a live local Ollama API. Windows may reserve 11434/11435, so the port moves."""
import os
import subprocess
import time
import urllib.error
import urllib.request

PROBE_SEC = 0.4
CACHE_SEC = 20
COMMON_PORTS = (11434, 11435, 11635, 11800, 18000)

_cache = {"url": None, "until": 0.0}


def _run(args):
    kw = {
        "timeout": 2,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.check_output(args, **kw).decode("utf-8", errors="replace")


def origin(url):
    text = (url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    text = text.rstrip("/")
    for suffix in ("/api/generate", "/api/chat", "/api/tags", "/v1"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].rstrip("/")
    return text.replace("://0.0.0.0", "://127.0.0.1").replace("://[::]", "://127.0.0.1")


def _alive(base):
    if not base:
        return False
    request = urllib.request.Request(
        base + "/api/tags",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=PROBE_SEC) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _env_origin():
    return origin(os.environ.get("OLLAMA_HOST", ""))


def _ollama_pids():
    pids = set()
    try:
        if os.name == "nt":
            raw = _run(["tasklist", "/FI", "IMAGENAME eq ollama.exe", "/FO", "CSV", "/NH"])
            for line in raw.splitlines():
                parts = [p.strip().strip('"') for p in line.split(",")]
                if len(parts) >= 2 and parts[1].isdigit():
                    pids.add(parts[1])
        else:
            raw = _run(["pgrep", "-x", "ollama"])
            pids.update(p.strip() for p in raw.split() if p.strip().isdigit())
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass
    return pids


def _listen_origins(pids):
    if not pids:
        return []
    found = []
    try:
        raw = _run(["netstat", "-ano", "-p", "tcp"] if os.name == "nt" else ["netstat", "-ltnp"])
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return found
    for line in raw.splitlines():
        if "LISTEN" not in line.upper():
            continue
        if os.name == "nt":
            parts = line.split()
            if len(parts) < 5 or parts[-1] not in pids:
                continue
            local = parts[1]
        else:
            if not any(f"pid={pid}" in line or f"/{pid}/" in line for pid in pids):
                continue
            parts = line.split()
            local = parts[3] if len(parts) > 3 else ""
        host, sep, port = local.rpartition(":")
        if not sep or not port.isdigit():
            continue
        if host in ("0.0.0.0", "*", "[::]", "::"):
            host = "127.0.0.1"
        elif host.startswith("[") and host.endswith("]"):
            host = "127.0.0.1"
        found.append(f"http://{host}:{port}")
    return found


def _candidates(hint=""):
    seen = set()
    ordered = []
    for item in (
        origin(hint),
        _env_origin(),
        *_listen_origins(_ollama_pids()),
        *(f"http://127.0.0.1:{port}" for port in COMMON_PORTS),
    ):
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def discover(hint=""):
    now = time.monotonic()
    cached = _cache["url"]
    if cached and now < _cache["until"]:
        return cached

    for base in _candidates(hint):
        if _alive(base):
            _cache["url"] = base
            _cache["until"] = now + CACHE_SEC
            return base

    _cache["url"] = None
    _cache["until"] = now + 5
    return ""


def resolve_configured(configured=""):
    """Rewrite a saved Ollama URL to a live origin. Keep /v1 when the saved URL used it."""
    text = (configured or "").strip()
    want_v1 = "/v1" in text.rstrip("/")
    live = discover(text)
    if not live:
        return text
    return f"{live}/v1" if want_v1 else live


def apply_if_ollama(provider, base_url):
    name = (provider or "").lower()
    if name != "ollama" and "ollama" not in name:
        return base_url
    return resolve_configured(base_url) or base_url
