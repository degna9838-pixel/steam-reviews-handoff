import pytest
from cs2_reviews.cleaning import clean_review_text


def test_text_views_preserve_sentiment_markup_and_paragraphs():
    raw = "[b]CS2[/b] is FUN, but VAC is not good!\nServers are lagging 😡 &amp; crashing."
    row = clean_review_text(raw)
    assert row["text_raw"] == raw
    assert row["text_clean"] == "CS2 is FUN, but VAC is not good!\nServers are lagging 😡 & crashing."
    assert row["tokens_trend"] == ["cs2", "fun", "vac", "not", "good", "server", "lag", "crash"]
    assert row["usable_for_sentiment"]


def test_negative_contractions_and_game_tokens():
    row = clean_review_text("I can't play CS2. VAC doesn't work; no 128 tick, AK-47, FPS 1.2.3!")
    assert row["text_clean"].startswith("I can't")
    assert row["tokens_trend"].count("not") == 2
    for token in ("cs2", "vac", "no", "128-tick", "ak-47", "fps", "1.2.3"):
        assert token in row["tokens_trend"]


def test_repeat_tokens_are_not_deduplicated_and_urls_not_tokenized():
    row = clean_review_text("lag lag lag https://example.com/foo VAC VAC")
    assert row["tokens_trend"].count("lag") == 3
    assert row["tokens_trend"].count("vac") == 2
    assert "example.com" not in row["tokens_trend"]


@pytest.mark.parametrize("text", ["gg", "bad", "not good", "VAC", "cs2"])
def test_short_domain_reviews_are_retained(text):
    row = clean_review_text(text)
    assert row["language_check"] == "uncertain"
    assert row["usable_for_sentiment"]
    assert row["usable_for_trend"]


@pytest.mark.parametrize("text", ["", "  \n\t", "!!! 😡😡", "1234 ---"])
def test_non_textual_records_retained_but_excluded(text):
    row = clean_review_text(text)
    assert row["text_raw"] == text
    assert not row["usable_for_sentiment"]
    assert not row["usable_for_trend"]
    assert row["language_check"] == "rejected"


@pytest.mark.parametrize("text", ["这个游戏非常好玩但是有很多外挂", "Игра очень плохая и постоянно зависает"])
def test_non_latin_reviews_are_excluded_even_with_english_api_label(text):
    row = clean_review_text(text)
    assert row["language_check"] == "rejected"
    assert "non_english_text" in row["quality_flags"]
    assert not row["usable_for_sentiment"]


def test_latin_non_english_review_is_rejected():
    row = clean_review_text("Ce jeu est vraiment mauvais et les serveurs fonctionnent très mal tous les jours.")
    assert row["language_check"] == "rejected"
    assert not row["usable_for_trend"]


def test_insufficient_mixed_english_is_flagged_and_not_sentiment_input():
    row = clean_review_text("CS2 is good 真好")
    assert row["language_check"] == "uncertain"
    assert "mixed_language_suspected" in row["quality_flags"]
    assert not row["usable_for_sentiment"]


def test_non_english_api_label_is_excluded():
    row = clean_review_text("This game is fun", "french")
    assert row["language_check"] == "rejected"
    assert "api_language_not_english" in row["quality_flags"]


def test_unicode_normalization_controls_and_emoji_sequence():
    row = clean_review_text("Cafe\u0301\x00 is\tgood! 👨\u200d💻")
    assert row["text_clean"] == "Café is good! 👨\u200d💻"


def test_stopwords_only_can_still_be_sentiment_input():
    row = clean_review_text("the and is")
    assert row["tokens_trend"] == []
    assert not row["usable_for_trend"]
    assert row["usable_for_sentiment"]


def test_deterministic_processing_and_invalid_input():
    text = "I really enjoy this game despite the occasional problem with the servers."
    assert clean_review_text(text) == clean_review_text(text)
    with pytest.raises(TypeError):
        clean_review_text(None)


def test_ascii_art_is_flagged_and_not_treated_as_prose():
    row = clean_review_text(" /\\_/\\\n( o.o )\n > ^ < ")
    assert "ascii_art_suspected" in row["quality_flags"]
    assert not row["usable_for_trend"]
    assert not row["usable_for_sentiment"]


@pytest.mark.parametrize("text", ["I dont recommend it", "I don t recommend it", "I don’t recommend it"])
def test_negation_spelling_preserves_trend_meaning(text):
    row = clean_review_text(text)
    assert "not" in row["tokens_trend"] and "don" not in row["tokens_trend"]
    assert row["text_clean"] == text


@pytest.mark.parametrize("text", ["muito bom", "eu odeio esse jogo", "güzel oyun", "Juego qlo mierda", "game rusak"])
def test_real_foreign_short_reviews_are_excluded(text):
    row = clean_review_text(text)
    assert row["text_raw"] == text
    assert row["language_check"] == "rejected", row
    assert not row["usable_for_sentiment"] and not row["usable_for_trend"]


@pytest.mark.parametrize("text", ["nice", "FUN", "good game", "I need this", "Better than life", "amazing for children under 2"])
def test_valid_english_short_reviews_stay_eligible(text):
    row = clean_review_text(text)
    assert row["usable_for_sentiment"] and row["usable_for_trend"], row


@pytest.mark.parametrize("text", ["jjfg", "bahright"])
def test_unclear_single_word_is_not_guessed(text):
    row = clean_review_text(text)
    assert row["text_raw"] == row["text_clean"] == text
    assert row["language_check"] == "uncertain"
    assert "unclear_semantics" in row["quality_flags"]
    assert not row["usable_for_sentiment"] and not row["usable_for_trend"]


def test_substantial_foreign_paragraph_not_silently_extracted():
    text = "Very good game for playing with friends , isuggest buy prime to get full of the game ❤️\r\nبازیش عالیه پرایم بخرین می ارزه"
    row = clean_review_text(text)
    assert row["text_raw"] == text and "بازیش" in row["text_clean"]
    assert row["usable_for_trend"] and not row["usable_for_sentiment"]
    assert "mixed_language_sentiment_excluded" in row["quality_flags"]


def test_insufficient_english_does_not_discard_foreign_sentiment():
    row = clean_review_text("hello! My sorry... play, гавно")
    assert "гавно" in row["text_clean"]
    assert not row["usable_for_trend"] and not row["usable_for_sentiment"]


def test_styled_english_is_normalized_and_not_flagged_foreign():
    raw = "Its fun and i like playing game𝐻𝒾 𝒾𝓂 𝒸𝒽𝓊𝒸𝓀 𝓃𝑜𝓇𝓇𝒾𝓈s\t"
    row = clean_review_text(raw)
    assert row["text_raw"] == raw
    assert "game Hi im chuck norriss" in row["text_clean"]
    assert "unicode_style_normalized" in row["quality_flags"]
    assert "mixed_language_suspected" not in row["quality_flags"]
    assert "chuck" in row["tokens_trend"]


def test_lists_and_paragraph_boundaries_remain():
    raw = "[list][*]no anticheat[*]no gameplay changes[/list]\n\nEDIT: better now 😡"
    row = clean_review_text(raw)
    assert "- no anticheat\n- no gameplay changes" in row["text_clean"]
    assert "\n\nEDIT: better now 😡" in row["text_clean"]
    ratings = clean_review_text("10/10\n5/10")
    assert ratings["text_clean"] == "10/10\n5/10"
    assert "ascii_art_removed" not in ratings["quality_flags"]


def test_domain_aliases_and_word_roles():
    row = clean_review_text("Good game but bad anti cheat; anticheat, anti-cheat. Cheaters cheated using cheats and got cooldowns.")
    assert row["tokens_trend"].count("anticheat") == 3
    assert row["tokens_trend"].count("cheat") == 2
    assert "cheater" in row["tokens_trend"] and "cooldown" in row["tokens_trend"]


def test_general_lemmas_preserve_comparison_and_game_names():
    row = clean_review_text("Friends are making changes in Mirage. Better, best, worse, worst. VAC, FACEIT, Valorant, CS2.")
    for token in ("friend", "make", "change", "mirage", "better", "best", "worse", "worst", "vac", "faceit", "valorant", "cs2"):
        assert token in row["tokens_trend"], row
    assert "sing" in clean_review_text("I enjoy singing sweet songs.")["tokens_trend"]
    shorthand = clean_review_text("OG VAC AI: pls fix this game, plz, my CPU and GPU are fine.")
    for token in ("og", "vac", "ai", "cpu", "gpu"):
        assert token in shorthand["tokens_trend"]
    assert shorthand["tokens_trend"].count("please") == 2
    assert "oganesson" not in shorthand["tokens_trend"] and "pl" not in shorthand["tokens_trend"]
    assert clean_review_text("GGWP!")["usable_for_trend"]
    assert clean_review_text("CS:GO")["tokens_trend"] == ["csgo"]
    assert "your" not in clean_review_text("Please fix ur game, Valve.")["tokens_trend"]


def test_numbers_keep_identifiers_but_remove_counts_and_ratings():
    raw = "CS2 and Counter-Strike 1.6 with AK-47 and 128 tick: 10/10, 100%, 9 hours, 5 kills. Version 2.0 and v1.2.3."
    row = clean_review_text(raw)
    assert row["text_clean"] == raw
    for token in ("cs2", "1.6", "ak-47", "128-tick", "2.0", "v1.2.3"):
        assert token in row["tokens_trend"], row
    for token in ("10", "100", "9", "5"):
        assert token not in row["tokens_trend"]


def test_joined_quantities_do_not_form_false_trend_words():
    row = clean_review_text("I played 20hours and paid 10euro, 1.5k rating, 128tick, CS2, 2v2. I aint happy.")
    for token in ("hour", "euro", "128-tick", "cs2", "2v2", "not"):
        assert token in row["tokens_trend"], row
    for token in ("20hours", "10euro", "1.5k"):
        assert token not in row["tokens_trend"]


@pytest.mark.parametrize("text, expected", [
    ("Dota 2", ["dota2"]),
    ("Phantom Blade Zero", ["phantom", "blade", "zero"]),
    ("Invoker BKB MMR", ["invoker", "bkb", "mmr"]),
])
def test_new_game_names_and_terms_remain_usable(text, expected):
    row = clean_review_text(text)
    assert row["text_raw"] == row["text_clean"] == text
    assert row["usable_for_trend"] and row["usable_for_sentiment"]
    assert row["tokens_trend"] == expected
