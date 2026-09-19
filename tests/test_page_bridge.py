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
