"""Only the existing bridge's log GET is supported; no network implementation."""
from stub_store import call


class Response:
    def raise_for_status(self):
        pass

    def json(self):
        return {"data": {"logs": [], "has_more": False}}


def get(url, **kwargs):
    assert url == "fixture://logs"
    call("logs")
    return Response()
