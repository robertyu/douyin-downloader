import asyncio

from cli.page_bridge import PlaywrightPageBridge


class _Page:
    def __init__(self):
        self.arguments = None

    async def evaluate(self, _script, arguments):
        self.arguments = arguments
        return {"http_status": 200, "body": {"aweme_list": []}, "text": "ok"}


async def test_fetch_runs_request_inside_douyin_page():
    bridge = PlaywrightPageBridge({})
    bridge._page = _Page()

    result = await bridge.fetch(
        "/aweme/v1/web/aweme/post/",
        {"sec_user_id": "a b", "count": 20},
    )

    assert bridge._page.arguments == {
        "url": (
            "https://www.douyin.com/aweme/v1/web/aweme/post/"
            "?sec_user_id=a+b&count=20"
        ),
        "method": "GET",
        "data": {},
    }
    assert result.http_status == 200
    assert result.body == {"aweme_list": []}


def test_detail_from_page_scripts_finds_matching_aweme():
    scripts = [
        '{"loader":{"item":{"aweme_id":"123","video":{"play_addr":{}}}}}',
    ]

    result = PlaywrightPageBridge._detail_from_scripts(scripts, "123")

    assert result == {
        "status_code": 0,
        "aweme_detail": {"aweme_id": "123", "video": {"play_addr": {}}},
    }


def test_detail_from_page_scripts_accepts_router_assignment():
    scripts = [
        'window._ROUTER_DATA = {"loader":{"item":{"awemeId":"456","images":[]}}};',
    ]

    result = PlaywrightPageBridge._detail_from_scripts(scripts, "456")

    assert result["aweme_detail"]["awemeId"] == "456"


def test_detail_page_falls_back_immediately_after_api_rejection():
    class Response:
        url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        status = 403

    class Page:
        url = "https://www.douyin.com/jingxuan"

        def on(self, _event, callback):
            self.callback = callback

        async def goto(self, *_args, **_kwargs):
            self.callback(Response())

        async def title(self):
            return "blocked"

        async def close(self):
            pass

    class Context:
        async def new_page(self):
            return Page()

    async def run():
        bridge = PlaywrightPageBridge({})
        bridge._context = Context()
        bridge._fetch_aweme_detail_share_page = lambda _aweme_id: asyncio.sleep(
            0, result={"status_code": 0, "aweme_detail": {"aweme_id": "123"}}
        )
        return await asyncio.wait_for(bridge._fetch_aweme_detail_page("123"), timeout=1)

    result = asyncio.run(run())

    assert result.body["aweme_detail"]["aweme_id"] == "123"
