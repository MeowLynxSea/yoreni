"""Link previews: what a card can learn about a page without a browser."""

import pytest

from yoreni.utils.link_preview import (
    LinkPreview,
    fetch_link_preview,
    host_label,
    is_http_url,
    link_payload,
    preview_from_html,
)


def test_open_graph_tags_win_over_the_document_title() -> None:
    html = (
        "<html><head>"
        "<title>Fallback title</title>"
        '<meta name="description" content="plain description">'
        '<meta property="og:title" content="Card title">'
        '<meta property="og:description" content="Card description">'
        '<meta property="og:site_name" content="Example">'
        "</head><body>text</body></html>"
    )

    preview = preview_from_html(html, "https://www.example.com/post")

    assert preview.title == "Card title"
    assert preview.description == "Card description"
    assert preview.site == "Example"
    assert preview.url == "https://www.example.com/post"


def test_a_plain_page_falls_back_to_its_title_and_host() -> None:
    preview = preview_from_html(
        "<html><head><title>\n  Plain   page\n</title></head></html>",
        "https://www.example.com/x",
    )

    assert preview.title == "Plain page"
    assert preview.site == "example.com"
    assert preview.description == ""


def test_a_page_without_metadata_keeps_the_card_usable() -> None:
    preview = preview_from_html("<html><head></head><body>nothing</body></html>", "https://docs.example.org/a")

    assert preview.title == "docs.example.org"
    assert preview.site == "docs.example.org"


def test_long_metadata_is_trimmed_instead_of_flooding_the_card() -> None:
    preview = preview_from_html(f"<html><head><title>{'x' * 400}</title></head></html>", "https://example.com")

    assert len(preview.title) == 200
    assert preview.title.endswith("…")


def test_entities_and_whitespace_are_normalized() -> None:
    preview = preview_from_html(
        '<html><head><meta property="og:title" content="Tom &amp; Jerry&#9;cartoon"></head></html>',
        "https://example.com",
    )

    assert preview.title == "Tom & Jerry cartoon"


def test_host_label_drops_www() -> None:
    assert host_label("https://www.example.com/a?b=1") == "example.com"
    assert host_label("https://example.com") == "example.com"
    assert is_http_url("ftp://example.com") is False


def test_link_payload_is_the_wire_card() -> None:
    payload = link_payload(LinkPreview(url="https://e.com", title="T", description="D", site="e.com"))

    assert payload == {
        "kind": "link",
        "url": "https://e.com",
        "name": "T",
        "title": "T",
        "description": "D",
        "site": "e.com",
    }


@pytest.mark.asyncio
async def test_private_targets_are_never_fetched() -> None:
    preview = await fetch_link_preview("http://127.0.0.1:8080/admin")

    assert preview.title == "127.0.0.1"
    assert preview.description == ""


@pytest.mark.asyncio
async def test_a_non_http_value_keeps_the_card_it_can_build() -> None:
    preview = await fetch_link_preview("file:///etc/passwd")

    assert preview.url == "file:///etc/passwd"
    assert is_http_url("file:///etc/passwd") is False

