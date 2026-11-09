from __future__ import annotations

import json

import pytest

from sentiment_analysis.entities import load_entity_config, match_entities, split_clauses


def ids(text):
    return {match.entity.entity_id for match in match_entities(text, load_entity_config(730))}


def test_split_clauses_handles_contrast_and_newlines():
    clauses = split_clauses("[b]CS2[/b] is fun, but VAC is not good.\nServers are lagging!")
    assert clauses == ["[b]CS2[/b] is fun", "but VAC is not good.", "Servers are lagging!"]


def test_split_clauses_handles_commas_and_short_fragments():
    assert split_clauses("CS2, fun") == ["CS2, fun"]
    assert split_clauses("Gameplay is great, graphics are bad.") == [
        "Gameplay is great", "graphics are bad."
    ]


def test_split_clauses_keeps_decimal_commas():
    assert split_clauses("This is 1,000 times better.") == ["This is 1,000 times better."]


def test_overall_game_always_matches_but_aspects_need_mentions():
    assert ids("gg") == {"game"}
    assert ids("not good") == {"game"}


def test_alias_word_boundaries_prevent_vacation_false_positive():
    assert "anticheat" not in ids("I went on vacation and had a good time.")


def test_multiple_entities_and_opposite_clauses():
    matches = {match.entity.entity_id: match for match in match_entities(
        "The new case has nice skins but the price is too expensive.", load_entity_config(730)
    )}
    assert "skins_economy" in matches
    assert "monetization" in matches
    assert "nice skins" in matches["skins_economy"].evidence
    assert "too expensive" in matches["monetization"].evidence


def test_unmentioned_aspects_are_not_returned():
    matches = ids("Servers are lagging and VAC is bad.")
    assert {"game", "servers", "anticheat"}.issubset(matches)
    assert "matchmaking" not in matches
    assert "monetization" not in matches


def test_config_rejects_duplicate_aliases(tmp_path):
    payload = {
        "config_version": "test-v1",
        "app_id": 730,
        "game": "CS2",
        "entities": [
            {"entity_id": "a", "name": "A", "scope": "clause", "aliases": ["shared"]},
            {"entity_id": "b", "name": "B", "scope": "clause", "aliases": ["shared"]},
        ],
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="Alias"):
        load_entity_config(730, path)


def test_config_rejects_wrong_app_id(tmp_path):
    payload = {
        "config_version": "test-v1",
        "app_id": 570,
        "game": "Dota 2",
        "entities": [
            {"entity_id": "game", "name": "Dota", "scope": "document", "aliases": ["dota"]},
        ],
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="app_id"):
        load_entity_config(730, path)
