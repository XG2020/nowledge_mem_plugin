import asyncio
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from nekro_agent.core import logger

from .plugin import PluginConfig, get_memory_config


def _build_url(base_url: str, path: str, params: Optional[Dict[str, Any]] = None) -> str:
    base = base_url.rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    url = f"{base}{suffix}"
    if params:
        # Normalize query params to match API expectations
        # - Booleans must be lowercase 'true'/'false'
        # - None values are omitted
        # - Lists/tuples expand with doseq=True
        norm_params: Dict[str, Any] = {}
        for k, v in params.items():
            if v is None:
                continue
            if isinstance(v, bool):
                norm_params[k] = "true" if v else "false"
            else:
                norm_params[k] = v
        if norm_params:
            url = f"{url}?{urllib.parse.urlencode(norm_params, doseq=True)}"
    return url


def _build_headers(config: PluginConfig, has_body: bool) -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if has_body:
        headers["Content-Type"] = "application/json"
    api_key = (config.NMEM_API_KEY or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    return headers


async def request_json(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Any, Optional[str]]:
    config = get_memory_config()
    url = _build_url(config.NMEM_API_URL, path, params)
    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    headers = _build_headers(config, json_body is not None)

    def _do_request() -> Tuple[bool, Any, Optional[str]]:
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        attempts = 0
        last_err: Optional[str] = None
        while attempts < 2:
            try:
                with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT_SECONDS) as resp:
                    payload = resp.read().decode("utf-8")
                if not payload:
                    return True, None, None
                try:
                    return True, json.loads(payload), None
                except Exception:
                    return True, payload, None
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8")
                except Exception:
                    body = ""
                return False, None, f"HTTP {e.code}: {body or e.reason}"
            except socket.timeout as e:
                last_err = f"timed out: {e}"
                attempts += 1
                logger.warning(f"请求超时，重试第 {attempts} 次: {method} {url}")
                continue
            except Exception as e:
                msg = str(e)
                if "timed out" in msg and attempts < 1:
                    last_err = msg
                    attempts += 1
                    logger.warning(f"请求疑似超时，重试第 {attempts} 次: {method} {url}")
                    continue
                return False, None, msg
        return False, None, last_err or "request failed"

    return await asyncio.to_thread(_do_request)


async def ensure_labels(labels: Optional[list]) -> None:
    config = get_memory_config()
    if not config.AUTO_CREATE_LABELS:
        return
    if not labels:
        return
    for label in labels:
        if not label:
            continue
        ok, _, err = await request_json("POST", "/labels", params={"name": label})
        if not ok and err:
            logger.warning(f"创建标签失败: {label} {err}")
