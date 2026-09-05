from datetime import timedelta

import pytest

import snapshot_refresh as sr


def test_normalize_quote_request_accepts_valid_request():
    item = sr.normalize_quote_request({
        "request_id": "newcastle-o15",
        "event_id": "123",
        "label": "Newcastle + over 1.5",
        "selections": [
            {"market_id": "10", "selection_id": "101"},
            {"market_id": 20, "selection_id": 202},
        ],
    })
    assert item["event_id"] == 123
    assert item["selections"] == [
        {"market_id": 10, "selection_id": 101},
        {"market_id": 20, "selection_id": 202},
    ]


def test_normalize_quote_request_rejects_duplicate_selection():
    with pytest.raises(ValueError, match="distinct"):
        sr.normalize_quote_request({
            "request_id": "bad",
            "event_id": 123,
            "selections": [
                {"market_id": 10, "selection_id": 101},
                {"market_id": 20, "selection_id": 101},
            ],
        })


def test_mycombi_refresh_age():
    now = sr.now_warsaw()
    fresh = {"fetched_at_warsaw": (now - timedelta(seconds=30)).isoformat()}
    stale = {"fetched_at_warsaw": (now - timedelta(seconds=sr.MYCOMBI_REFRESH_SECONDS + 1)).isoformat()}
    assert sr.should_refresh_mycombi(fresh, now) is False
    assert sr.should_refresh_mycombi(stale, now) is True
    assert sr.should_refresh_mycombi(None, now) is True


def test_atomic_write_round_trip(tmp_path):
    path = tmp_path / "nested" / "data.json"
    sr.atomic_write_json(path, {"ą": [1, 2, 3]})
    assert sr.load_json(path, {}) == {"ą": [1, 2, 3]}
    assert not list(path.parent.glob("*.tmp"))


def test_validate_snapshot_rejects_duplicate_event_ids():
    today = sr.now_warsaw().date().isoformat()
    snapshot = {
        "schema_version": 3,
        "date_warsaw": today,
        "sports": {
            "football": {"events": [{"id": 1}, {"id": 1}]},
            "tennis": {"events": []},
        },
    }
    with pytest.raises(ValueError, match="duplicate football"):
        sr.validate_snapshot(snapshot, today)


def test_validate_snapshot_accepts_summary_only_event():
    today = sr.now_warsaw().date().isoformat()
    snapshot = {
        "schema_version": 3,
        "date_warsaw": today,
        "sports": {
            "football": {"events": [{"id": 1, "name": "A - B"}]},
            "tennis": {"events": []},
        },
    }
    sr.validate_snapshot(snapshot, today)
