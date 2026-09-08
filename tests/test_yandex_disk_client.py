"""Network-free tests for the embedded YandexDiskClient."""

from pathlib import Path
from unittest.mock import Mock, patch

import requests
import pytest

from syncbase.client import YandexDiskClient


def response(status: int, payload: dict | None = None) -> Mock:
    result = Mock()
    result.status_code = status
    result.headers = {}
    if payload is not None:
        result.json.return_value = payload
    return result


def test_init():
    client = YandexDiskClient("test-token")

    assert client.token == "test-token"
    assert client.api_base == "https://cloud-api.yandex.net/v1/disk/resources"
    assert client.headers == {"Authorization": "OAuth test-token"}


@patch("syncbase.client.requests.request")
def test_list_paginates(mock_request):
    mock_request.side_effect = [
        response(
            200,
            {
                "_embedded": {
                    "items": [{"name": "one", "type": "dir"}],
                    "total": 2,
                }
            },
        ),
        response(
            200,
            {
                "_embedded": {
                    "items": [{"name": "two", "type": "dir"}],
                    "total": 2,
                }
            },
        ),
    ]

    assert [item["name"] for item in YandexDiskClient("token").list("app:/", limit=1)] == [
        "one",
        "two",
    ]


@pytest.mark.parametrize("status", [None, 401, 429, 500])
def test_list_failure_does_not_look_like_empty_folder(status):
    client = YandexDiskClient("token")
    with patch.object(client, "_make_request", return_value=response(status) if status else None):
        with pytest.raises(RuntimeError, match="Не удалось получить список"):
            client.list("app:/Project")


@pytest.mark.parametrize("status", [None, 404, 500])
def test_list_failure_on_later_page_does_not_return_partial_tree(status):
    client = YandexDiskClient("token")
    pages = [
        response(200, {"_embedded": {"items": [{"name": "one", "type": "file"}], "total": 2}}),
        response(status) if status else None,
    ]
    with patch.object(client, "_make_request", side_effect=pages):
        with pytest.raises(RuntimeError, match="Не удалось получить список"):
            client.list("app:/Project", limit=1)


def test_list_missing_project_is_empty():
    client = YandexDiskClient("token")
    with patch.object(client, "_make_request", return_value=response(404)):
        assert client.list("app:/Project") == []


@patch("syncbase.client.time.sleep")
@patch("syncbase.client.requests.put")
def test_raw_upload_retries_server_error(mock_put, mock_sleep, tmp_path: Path):
    source = tmp_path / "small.txt"
    source.write_bytes(b"raw file body")
    captured_bodies: list[bytes] = []

    def put_side_effect(_url, *, data, **_kwargs):
        captured_bodies.append(data.read())
        return response(500 if len(captured_bodies) == 1 else 201)

    mock_put.side_effect = put_side_effect

    result = YandexDiskClient("token")._upload_file_with_retry("https://upload", source)

    assert result.status_code == 201
    assert captured_bodies == [b"raw file body", b"raw file body"]
    assert "files" not in mock_put.call_args.kwargs
    assert mock_put.call_args.kwargs["headers"] == {
        "Content-Type": "application/octet-stream"
    }
    mock_sleep.assert_called_once()


@patch.object(YandexDiskClient, "move", return_value=True)
@patch.object(YandexDiskClient, "_upload_file_with_retry")
@patch.object(YandexDiskClient, "_make_request")
def test_small_upload_uses_retry_helper(mock_request, mock_upload, _mock_move, tmp_path: Path):
    source = tmp_path / "small.txt"
    source.write_bytes(b"content")
    mock_request.return_value = response(200, {"href": "https://upload"})
    mock_upload.return_value = response(201)

    result = YandexDiskClient("token").upload(
        source,
        "app:/Category/Project/small.txt",
        create_parent=False,
    )

    assert result is True
    mock_upload.assert_called_once_with("https://upload", source)


@patch.object(YandexDiskClient, "remove", return_value=True)
@patch.object(YandexDiskClient, "_upload_file_with_progress", return_value=False)
def test_failed_upload_removes_temporary_cloud_file(
    mock_upload,
    mock_remove,
    tmp_path: Path,
):
    source = tmp_path / "file.txt"
    source.write_text("data", encoding="utf-8")

    result = YandexDiskClient("token").upload(
        source,
        "app:/MyProject/file.txt",
        create_parent=False,
    )

    assert result is False
    mock_upload.assert_called_once()
    mock_remove.assert_called_once_with("app:/MyProject/file.txt.tmp")


@patch("syncbase.client.requests.request")
def test_exists_false_for_missing_resource(mock_request):
    mock_request.return_value = response(404)

    assert YandexDiskClient("token").exists("app:/missing") is False


@patch.object(YandexDiskClient, "_make_request")
def test_download_writes_response_body(mock_request, tmp_path: Path):
    signed_url = response(200, {"href": "https://download"})
    body = response(200)
    body.headers = {"content-length": "4"}
    body.content = b"data"
    mock_request.side_effect = [signed_url, body]
    destination = tmp_path / "file.bin"

    assert YandexDiskClient("token").download("app:/file.bin", destination) is True
    assert destination.read_bytes() == b"data"


@patch("syncbase.client.requests.put")
def test_upload_returns_none_after_network_failures(mock_put, tmp_path: Path):
    source = tmp_path / "file.txt"
    source.write_text("data", encoding="utf-8")
    mock_put.side_effect = requests.exceptions.ConnectionError("offline")

    with patch("syncbase.client.time.sleep"):
        assert (
            YandexDiskClient("token")._upload_file_with_retry(
                "https://upload", source, max_retries=1
            )
            is None
        )
