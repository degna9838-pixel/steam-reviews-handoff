from __future__ import annotations

from sentiment_analysis.entities import load_entity_config, match_entities
from sentiment_analysis.sentiment import SentimentAnalyzer


def scores(text):
    analyzer = SentimentAnalyzer()
    config = load_entity_config(730)
    return {
        match.entity.entity_id: analyzer.score_match(match)
        for match in match_entities(text, config)
    }


def test_negation_and_short_text():
    assert scores("not good")["game"][1] == "negative"
    assert scores("gg")["game"][1] == "positive"
    assert scores("CS2 is trash")["game"][1] == "negative"


def test_contrast_scopes_entities_separately():
    result = scores("The new case has nice skins but the price is too expensive.")
    assert result["skins_economy"][1] == "positive"
    assert result["monetization"][1] == "negative"


def test_anticheat_and_servers_detected():
    result = scores("CS2 is fun, but VAC is not good. Servers are lagging!")
    assert result["anticheat"][1] == "negative"
    assert result["servers"][1] == "negative"


def test_improvement_is_positive():
    result = scores("CS2 is fun, and the latest update improved VAC.")
    assert result["game"][1] == "positive"
    assert result["anticheat"][1] == "positive"


def test_domain_p2w_is_negative():
    result = scores("This game is p2w and the battle pass is expensive.")
    assert result["monetization"][1] == "negative"


def test_label_thresholds():
    assert SentimentAnalyzer.label_for(0.05) == "positive"
    assert SentimentAnalyzer.label_for(-0.05) == "negative"
    assert SentimentAnalyzer.label_for(0.049) == "neutral"
    assert SentimentAnalyzer.label_for(-0.049) == "neutral"
