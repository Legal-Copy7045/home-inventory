"""Errors from libraries and remote services stay in the log, not in responses."""

import httpx

from home_inventory_server.ai import providers
from home_inventory_server.services import manuals


def test_ai_service_rejections_keep_the_body_out_of_the_message():
    msg = providers._rejected("Gemini", 400, '{"error": "internal-detail-xyz"}')
    assert "internal-detail-xyz" not in msg and "400" in msg
    assert "API key" in providers._rejected("Gemini", 403, "nope")
    assert "ai_model" in providers._rejected("Gemini", 404, "nope")


def test_unreachable_service_message_has_no_exception_text():
    msg = providers._unreachable("Gemini", httpx.ConnectError("[Errno 111] 10.0.0.9:443 refused"))
    assert msg == "could not reach Gemini"


def test_bad_pdf_message_has_no_parser_detail():
    try:
        manuals.page_images(b"not a pdf at all", [1])
    except manuals.ManualError as err:
        assert str(err) == "This PDF couldn't be opened."
    else:  # pragma: no cover
        raise AssertionError("expected ManualError")
