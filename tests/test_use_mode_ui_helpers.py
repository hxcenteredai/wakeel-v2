"""Tests for the pure-function helpers in run_ui.py used by the use-mode panel.

We avoid spinning up Streamlit — only the non-st helpers are exercised so
this stays a fast unit test. Network calls are mocked.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

import run_ui


def test_looks_arabic_flags_arabic_only_when_present() -> None:
    assert run_ui._looks_arabic("ابنِ مساعدًا") is True
    assert run_ui._looks_arabic("Build me a copilot") is False
    assert run_ui._looks_arabic("") is False


def test_risk_badge_color_per_level() -> None:
    high = run_ui._risk_badge("high")
    med = run_ui._risk_badge("medium")
    low = run_ui._risk_badge("low")
    assert "#c0392b" in high
    assert "#d68910" in med
    assert "#1e8449" in low
    assert ">HIGH<" in high.upper()


def test_list_copilots_returns_empty_on_backend_error() -> None:
    with mock.patch("run_ui.requests.get", side_effect=RuntimeError("boom")):
        assert run_ui._list_copilots() == []


def test_list_copilots_parses_payload() -> None:
    fake = mock.Mock()
    fake.json.return_value = {
        "copilots": [
            {"copilot_id": "cp_abc", "template": "nda"},
            {"copilot_id": "cp_def", "template": "nda"},
        ]
    }
    fake.raise_for_status.return_value = None
    with mock.patch("run_ui.requests.get", return_value=fake):
        out = run_ui._list_copilots()
    assert [c["copilot_id"] for c in out] == ["cp_abc", "cp_def"]


def test_call_backend_posts_to_run_endpoint() -> None:
    fake = mock.Mock()
    fake.json.return_value = {"ok": True}
    fake.raise_for_status.return_value = None
    with mock.patch("run_ui.requests.post", return_value=fake) as posted:
        out = run_ui._call_backend({"mode": "use"})
    assert out == {"ok": True}
    args, kwargs = posted.call_args
    assert args[0].endswith("/run")
    assert kwargs["json"] == {"mode": "use"}


@pytest.mark.parametrize("label", list(run_ui.USE_MODE_EXAMPLES.keys()))
def test_load_use_example_returns_title_and_content(label: str) -> None:
    title, content = run_ui._load_use_example(label)
    assert isinstance(title, str) and title
    assert isinstance(content, str) and content
    # cross-check the on-disk file actually matches the loaded values
    path = Path(__file__).resolve().parent.parent / run_ui.USE_MODE_EXAMPLES[label]
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    assert payload["document"]["title"] == title
    assert payload["document"]["content"] == content
