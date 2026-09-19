"""Run gated Douyin API requests inside a real Playwright page."""

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlencode


@dataclass
class PageBridgeResult:
    http_status: int
    body: Optional[Dict[str, Any]]
    text: str


class PageBridgeError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.page_bridge_code = code


class PlaywrightPageBridge:
    """Lazy browser page whose Douyin SecSDK signs same-origin ``fetch`` calls."""

    def __init__(
        self,
        cookies: Dict[str, str],
        *,
        proxy: Optional[str] = None,
        headless: bool = False,
        timeout_seconds: int = 60,
    ):
        self.cookies = cookies
        self.proxy = str(proxy or "").strip()
        self.headless = headless
        self.timeout_ms = max(1, int(timeout_seconds)) * 1000
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._start_lock = asyncio.Lock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def _start(self) -> None:
        if self._page is not None:
            return
        async with self._start_lock:
            if self._page is not None:
                return
            try:
                from playwright.async_api import async_playwright
            except ImportError as exc:
                raise PageBridgeError(
                    "BROWSER_UNAVAILABLE",
                    "Playwright 未安装；请执行 pip install playwright && playwright install chromium",
                ) from exc

            try:
                self._playwright = await async_playwright().start()
                launch_options = {"headless": self.headless}
                if self.proxy:
                    launch_options["proxy"] = {"server": self.proxy}
                self._browser = await self._playwright.chromium.launch(**launch_options)
                self._context = await self._browser.new_context()
                if self.cookies:
                    await self._context.add_cookies(
                        [
                            {
                                "name": name,
                                "value": value,
                                "domain": ".douyin.com",
                                "path": "/",
                            }
                            for name, value in self.cookies.items()
                            if name and value
                        ]
                    )
                self._page = await self._context.new_page()
                await self._page.goto(
                    "https://www.douyin.com/",
                    wait_until="domcontentloaded",
                    timeout=self.timeout_ms,
                )
                # The page is interactive before its security SDK finishes installing hooks.
                await self._page.wait_for_timeout(1500)
            except Exception as exc:
                await self.close()
                raise PageBridgeError("PAGE_LOAD_FAILED", f"无法启动抖音签名页：{exc}") from exc

    async def fetch(
        self,
        path: str,
        params: Dict[str, Any],
        *,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
    ) -> PageBridgeResult:
        await self._start()
        if path == "/aweme/v1/web/aweme/detail/" and params.get("aweme_id"):
            return await self._fetch_aweme_detail_page(str(params["aweme_id"]))
        url = "https://www.douyin.com{}?{}".format(path, urlencode(params))
        try:
            result = await self._page.evaluate(
                """async ({url, method, data}) => {
                    const options = {
                        method,
                        credentials: "include",
                        headers: {Accept: "application/json, text/plain, */*"},
                    };
                    if (method === "POST") {
                        options.headers["Content-Type"] =
                            "application/x-www-form-urlencoded;charset=UTF-8";
                        options.body = new URLSearchParams(data || {}).toString();
                    }
                    const response = await fetch(url, options);
                    const text = await response.text();
                    let body = null;
                    try { body = JSON.parse(text); } catch (_) {}
                    return {http_status: response.status, body, text};
                }""",
                {"url": url, "method": method.upper(), "data": data or {}},
            )
        except Exception as exc:
            raise PageBridgeError("FETCH_FAILED", f"页面内请求失败：{exc}") from exc
        return PageBridgeResult(
            http_status=int(result.get("http_status") or 0),
            body=result.get("body") if isinstance(result.get("body"), dict) else None,
            text=str(result.get("text") or ""),
        )

    async def _fetch_aweme_detail_page(self, aweme_id: str) -> PageBridgeResult:
        """Let the real video page load the item instead of issuing synthetic fetch."""
        page = await self._context.new_page()
        captured = asyncio.get_running_loop().create_future()
        pending = []

        async def capture(response) -> None:
            if "/aweme/v1/web/aweme/detail/" not in (response.url or ""):
                return
            try:
                body = await response.json()
            except Exception:
                return
            if not captured.done() and isinstance(body, dict):
                captured.set_result(body)

        def on_response(response) -> None:
            pending.append(asyncio.create_task(capture(response)))

        page.on("response", on_response)
        try:
            await page.goto(
                "https://www.douyin.com/video/{}".format(aweme_id),
                wait_until="domcontentloaded",
                timeout=self.timeout_ms,
            )
            try:
                body = await asyncio.wait_for(asyncio.shield(captured), timeout=5)
            except asyncio.TimeoutError:
                scripts = await page.locator("script").all_text_contents()
                body = self._detail_from_scripts(scripts, aweme_id)
            if body:
                text = json.dumps(body, ensure_ascii=False)
                return PageBridgeResult(200, body, text)
            return PageBridgeResult(200, None, "")
        except Exception as exc:
            raise PageBridgeError("PAGE_LOAD_FAILED", f"无法读取抖音作品页：{exc}") from exc
        finally:
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await page.close()

    @classmethod
    def _detail_from_scripts(cls, scripts, aweme_id: str) -> Optional[Dict[str, Any]]:
        for text in scripts:
            for candidate in (text, unquote(text)):
                try:
                    data = json.loads(candidate)
                except (TypeError, ValueError):
                    continue
                item = cls._find_aweme(data, aweme_id)
                if item is not None:
                    return {"status_code": 0, "aweme_detail": item}
        return None

    @classmethod
    def _find_aweme(cls, value: Any, aweme_id: str) -> Optional[Dict[str, Any]]:
        if isinstance(value, dict):
            if str(value.get("aweme_id") or value.get("awemeId") or "") == aweme_id:
                return value
            for child in value.values():
                found = cls._find_aweme(child, aweme_id)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = cls._find_aweme(child, aweme_id)
                if found is not None:
                    return found
        return None

    async def close(self) -> None:
        for resource, method in (
            (self._context, "close"),
            (self._browser, "close"),
            (self._playwright, "stop"),
        ):
            if resource is None:
                continue
            try:
                await getattr(resource, method)()
            except Exception:
                pass
        self._page = self._context = self._browser = self._playwright = None
