"""Borrowed local attachments share ingress policy without taking file ownership."""

from unittest.mock import Mock

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request

from yoreni.channels.websocket.attachment_policy import AttachmentIngressLimits
from yoreni.security.workspace_access import default_workspace_scope
from yoreni.webui.attachments import inspect_local_attachment
from yoreni.webui.file_preview import WebUIFilePreviewError
from yoreni.webui.media_gateway import WebUIMediaGateway


def test_inspect_literal_relative_path_without_reading_or_copying(tmp_path, monkeypatch):
    source = tmp_path / "clip #100%.mp4"
    source.write_bytes(b"\0video")
    monkeypatch.setattr(type(source), "read_bytes", lambda *_: pytest.fail("inspection read file body"))
    scope = default_workspace_scope(tmp_path, True)
    result = inspect_local_attachment(source.name, scope=scope, limits=AttachmentIngressLimits())
    assert result == {"path": str(source), "name": source.name, "size": 6, "mime": "video/mp4", "kind": "video"}
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [source]


@pytest.mark.parametrize("name", ["image.png", "notes.md", "document.pdf", "video.mov", "video.webm"])
def test_local_supported_formats_do_not_consume_upload_byte_limits(tmp_path, name):
    source = tmp_path / name
    size = 64 * 1024 * 1024 + 1
    with source.open("wb") as output:
        output.truncate(size)
    limits = AttachmentIngressLimits(max_file_bytes=3, max_total_bytes=3)
    assert inspect_local_attachment(name, scope=default_workspace_scope(tmp_path, True), limits=limits)["size"] == size


def test_reject_missing_directory_outside_empty_and_unreadable(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    source = tmp_path / "outside.mp4"
    source.write_bytes(b"x")
    scope = default_workspace_scope(workspace, True)
    for path, status in [("missing.mp4", 404), (str(workspace), 404), (str(source), 403)]:
        with pytest.raises(WebUIFilePreviewError) as error:
            inspect_local_attachment(path, scope=scope, limits=AttachmentIngressLimits())
        assert error.value.status == status
    video = workspace / "large.mp4"
    video.touch()
    with pytest.raises(WebUIFilePreviewError, match="[Ee]mpty"):
        inspect_local_attachment(str(video), scope=scope, limits=AttachmentIngressLimits())
    monkeypatch.setattr("yoreni.webui.attachments.os.open", Mock(side_effect=PermissionError))
    with pytest.raises(WebUIFilePreviewError, match="readable"):
        inspect_local_attachment(str(video), scope=scope, limits=AttachmentIngressLimits())


def test_local_media_ranges_latest_contents_without_staging(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "original.mp4"
    source.write_bytes(b"first video")
    media = tmp_path / "media"
    gateway = WebUIMediaGateway(workspace_path=project, logger=Mock(), media_dir=lambda _: media)
    attachments = gateway.augment_transcript_media([str(source)])
    sig, payload = attachments[0]["url"].split("/")[-2:]

    def fetch(range_header=None):
        return gateway.serve_signed_media(
            sig, payload,
            request=Request("/", Headers({"Range": range_header} if range_header else {})),
        )

    assert bytes(fetch().body) == b"first video"
    source.write_bytes(b"latest video")
    response = fetch("bytes=0-5")
    assert response.status_code == 206
    assert bytes(response.body) == b"latest"
    assert response.headers["Cache-Control"] == "no-store"
    assert not media.exists()
    source.unlink()
    assert fetch().status_code == 404
    assert gateway.augment_transcript_media([str(source)])[0]["url"] == ""


def test_referenced_file_in_media_storage_is_not_treated_as_immutable_upload(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    source = media / "original.mp4"
    source.write_bytes(b"before")
    gateway = WebUIMediaGateway(workspace_path=tmp_path, logger=Mock(), media_dir=lambda _: media)
    attachment = gateway.augment_transcript_media([str(source)])[0]
    sig, payload = attachment["url"].split("/")[-2:]
    source.write_bytes(b"updated")
    response = gateway.serve_signed_media(sig, payload)
    assert bytes(response.body) == b"updated"
    assert response.headers["Cache-Control"] == "no-store"
    assert list(media.iterdir()) == [source]


def test_legacy_media_filename_starting_with_bracket_keeps_working(tmp_path):
    source = tmp_path / "[recording].mp4"
    source.write_bytes(b"uploaded video")
    gateway = WebUIMediaGateway(workspace_path=tmp_path, logger=Mock(), media_dir=lambda _: tmp_path)
    attachment = gateway.sign_or_stage_media_path(source)
    sig, payload = attachment["url"].split("/")[-2:]
    response = gateway.serve_signed_media(sig, payload)
    assert bytes(response.body) == b"uploaded video"


def test_replayed_media_keeps_missing_files_in_order_without_download_urls(tmp_path, monkeypatch):
    media = tmp_path / "media"
    media.mkdir()
    first = media / "first.json"
    missing = media / "missing.txt"
    last = media / "last.pdf"
    first.write_bytes(b'{"synthetic":true}')
    last.write_bytes(b"synthetic pdf")
    paths = [str(path) for path in (first, missing, last)]
    gateway = WebUIMediaGateway(workspace_path=tmp_path, logger=Mock(), media_dir=lambda _: media)

    def project():
        return gateway.augment_transcript_media(paths)

    with monkeypatch.context() as patch:
        patch.setattr(type(first), "read_bytes", lambda *_: pytest.fail("projection read file contents"))
        attachments = project()
    assert [item["name"] for item in attachments] == ["first.json", "missing.txt", "last.pdf"]
    assert attachments[1] == {"kind": "file", "name": "missing.txt", "url": ""}
    sig, payload = attachments[0]["url"].split("/")[-2:]
    assert bytes(gateway.serve_signed_media(sig, payload).body) == b'{"synthetic":true}'
    assert attachments[2]["url"]

    last.unlink()
    refreshed = project()
    assert [item["name"] for item in refreshed] == [item["name"] for item in attachments]
    assert refreshed[2]["url"] == ""
