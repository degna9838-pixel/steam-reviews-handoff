"""Versioned preprocessing with separate sentiment text and trend tokens.

Original reviews are immutable. Language decisions are conservative heuristics,
not human annotations. General English lemmas use the simplemma dictionary;
domain aliases/lemmas and intensity words take precedence.
"""
from __future__ import annotations

import html
import re
import unicodedata
from functools import lru_cache

from langdetect import DetectorFactory, LangDetectException, detect_langs
from simplemma import is_known, lemmatize

PIPELINE_VERSION = "clean-en-v3"
DetectorFactory.seed = 0
STOP_WORDS = frozenset("""a an the and or but if then than that this these those to
of in on at by for from with as into onto about above below over under through
during before after between among against up down out off again further here
there where when why how all any both each few more most other some such only
own same so too very just now also still even much many well really actually
i me my myself we us our ours ourselves you your yours yourself yourselves
he him his himself she her hers herself it its itself they them their theirs
themselves who whom whose which what am is are was were be been being do does
did doing have has had having can could will would shall should may might must
ought im ive id ill youre youve youll youd hes shes its were theyre theyve
theyll theyd thats theres heres lets ve ll re s t d m""".split())
DOMAIN_LEMMAS = {
    "servers": "server", "cheaters": "cheater", "cheating": "cheat",
    "cheats": "cheat", "cheated": "cheat", "cooldowns": "cooldown",
    "lagging": "lag", "lagged": "lag", "lags": "lag",
    "crashes": "crash", "crashing": "crash", "crashed": "crash",
    "updates": "update", "updated": "update", "updating": "update",
    "bans": "ban", "banned": "ban", "banning": "ban",
    "skins": "skin", "matches": "match", "bugs": "bug",
    "fixes": "fix", "fixed": "fix", "fixing": "fix",
    "players": "player", "played": "play", "playing": "play",
    "frames": "frame", "issues": "issue", "errors": "error",
    "maps": "map", "weapons": "weapon", "rounds": "round",
    "teammates": "teammate", "hackers": "hacker", "hacking": "hack",
    "stutters": "stutter", "stuttering": "stutter", "stuttered": "stutter",
    "disconnects": "disconnect", "disconnected": "disconnect",
    "disconnecting": "disconnect", "queues": "queue", "queued": "queue",
    "queueing": "queue", "queuing": "queue", "bots": "bot",
    "games": "game", "hours": "hour", "minutes": "minute", "lobbys": "lobby",
    "singing": "sing", "sang": "sing", "sung": "sing",
    "pls": "please", "plz": "please", "hrs": "hour", "hr": "hour",
}
PROTECTED_TERMS = frozenset("""cs cs2 csgo counterstrike counter-strike valorant
vac valve steam steamworks faceit premier competitive wingman matchmaking
anticheat fps tick subtick sub-tick elo mmr ct t awp ak-47 ak47 m4a1 m4a1-s m4a4
usp-s deagle dust2 dust-2 mirage inferno nuke vertigo anubis ancient overpass
cache train scout spinjitsu gg ggwp ez hl1 hl2 gaben microsoft ai og cpu gpu ram ssd
ui ux api iq mm afk brb lol lmao rofl smh tbh imo imho idk btw dota dota2
phantom blade zero pbz wuxia s-game moba bkb tp roshan invoker pudge juggernaut
offlane safelane midlane aghanim aegis dota-plus dpc better best worse worst
more most less least no not never nor neither without""".split())
_SHORT_ENGLISH_WORDS = frozenset("""good bad great terrible awful nice fun love
hate like dislike broken excellent amazing sucks worst best trash cool yes no
not never gg ez playable unplayable fix cheat cheater lag server update game
okay ok need keep get life enjoy enjoyable boring horrible frustrating scam
recommend recommended buy prime skill friend children rubbish shit bullshit
unfair hard easy play works work help please thanks thank you""".split())
_WEAK_BORROWINGS = frozenset("game games anti anticheat cheat cheats vac cs cs2 csgo fps".split())
_FOREIGN_CUES = frozenset("""muito bom ruim odeio jogo esse não nao obrigado
ótimo otimo juego mierda qlo malo bueno recomiendo jugar gracias salud
güzel guzel oyun yapiyorlar yaram gibi vermiyor oynamayin oynatmayin
rusak bagus banget buruk mantap sangat permainan sehr schlecht danke""".split())
_BB_TAG = re.compile(
    r"\[(?:/?(?:b|i|u|s|strike|spoiler|h[1-6]|quote|code|list|olist|table|tr|"
    r"td|th|url|img|previewyoutube|youtube|noparse|hr|p|center|left|right|color|"
    r"size|font)(?:[=\s][^\]]*)?|\*)\]", re.IGNORECASE)
_HTML_TAG = re.compile(
    r"</?(?:b|i|u|strong|em|br|p|div|span|a)(?:\s+[^<>]*)?/?>", re.IGNORECASE)
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
_TOKEN = re.compile(r"[^\W_]+(?:[.'-][^\W_]+)*", re.UNICODE)
_DRAW_CHARS = frozenset("/\\|_-=<>^()[]{}~`¯│┃┏┓┗┛─━┻┳┌┐└┘╭╮╰╯︵")
_ART_LETTERS = frozenset("フミノヽﾉつ二口ロoOlIxX")
_NEGATIVE_CONTRACTIONS = {
    "can't": "can not", "won't": "will not", "shan't": "shall not",
    "ain't": "is not", "aint": "is not", "cannot": "can not", "dont": "do not",
    "doesnt": "does not", "didnt": "did not", "isnt": "is not",
    "arent": "are not", "wasnt": "was not", "werent": "were not",
    "havent": "have not", "hasnt": "has not", "hadnt": "had not",
    "couldnt": "could not", "shouldnt": "should not", "wouldnt": "would not",
    "mustnt": "must not", "neednt": "need not", "cant": "can not",
}
_SPLIT_NEGATIVES = {
    "don": "do", "doesn": "does", "didn": "did", "isn": "is",
    "aren": "are", "wasn": "was", "weren": "were", "haven": "have",
    "hasn": "has", "hadn": "had", "couldn": "could", "shouldn": "should",
    "wouldn": "would", "mustn": "must", "needn": "need", "can": "can",
    "won": "will", "shan": "shall",
}


def _word_tokens(text: str) -> list[str]:
    text = re.sub(r"\bdota[ \t-]+2\b", "dota2", text, flags=re.I)
    text = re.sub(r"\bcs[ \t:-]+(2|go)\b", r"cs\1", text, flags=re.I)
    return _TOKEN.findall(text.lower().replace("\u2019", "'"))


@lru_cache(maxsize=8192)
def _known_english(word: str) -> bool:
    return (word in STOP_WORDS or word in PROTECTED_TERMS
            or word in _SHORT_ENGLISH_WORDS or word in DOMAIN_LEMMAS
            or is_known(word, lang="en"))


def _style_fold(text: str) -> tuple[str, bool]:
    result = []
    changed = False
    previous_styled = False
    for char in text:
        name = unicodedata.name(char, "")
        styled = ("MATHEMATICAL" in name or (0x2100 <= ord(char) <= 0x214F
                  and any(label in name for label in ("SCRIPT", "DOUBLE-STRUCK", "BLACK-LETTER"))))
        replacement = unicodedata.normalize("NFKC", char) if styled else char
        if styled and replacement != char:
            if not previous_styled and result and result[-1][-1:].isalnum():
                result.append(" ")
            changed = True
        result.append(replacement)
        previous_styled = styled
    return "".join(result), changed


def _art_candidate(line: str) -> bool:
    value = unicodedata.normalize("NFKC", line)
    visible = [char for char in value if not char.isspace()]
    if not visible:
        return False
    if any(char.isdigit() for char in visible):
        return False  # Ratings, scoreboards and arithmetic are not drawings.
    words = _word_tokens(value)
    if any(len(word) >= 2 and _known_english(word) for word in words):
        return False
    drawing = sum(char in _DRAW_CHARS for char in visible)
    if not drawing:
        return False
    prose_letters = [char for char in visible if char.isalpha() and char not in _ART_LETTERS]
    return not prose_letters and drawing / len(visible) >= 0.2


def _light_clean(text: str) -> tuple[str, list[str]]:
    flags = []
    value, styled = _style_fold(unicodedata.normalize("NFC", html.unescape(text)))
    if styled:
        flags.append("unicode_style_normalized")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"\[\*\]", "\n- ", value)
    value = re.sub(r"\[/?(?:list|olist|quote|h[1-6])(?:=[^\]]*)?\]", "\n", value, flags=re.I)
    value = _BB_TAG.sub(" ", value)
    value = re.sub(r"</?(?:br|p|div)(?:\s+[^<>]*)?/?>", "\n", value, flags=re.I)
    value = _HTML_TAG.sub(" ", value)
    value = "".join(char if char in "\n\t\u200d" or unicodedata.category(char) not in {"Cc", "Cf"}
                    else " " for char in value)
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in value.split("\n")]
    candidates = [_art_candidate(line) for line in lines]
    remove = set()
    start = 0
    while start < len(lines):
        if not candidates[start]:
            start += 1
            continue
        end = start + 1
        while end < len(lines) and (candidates[end] or not lines[end]):
            end += 1
        if sum(candidates[start:end]) >= 2:
            remove.update(i for i in range(start, end) if candidates[i])
        start = end
    if remove:
        flags.extend(["ascii_art_suspected", "ascii_art_removed"])
    value = "\n".join(line for i, line in enumerate(lines) if i not in remove)
    return re.sub(r"\n{3,}", "\n\n", value).strip(), flags


@lru_cache(maxsize=8192)
def _lemma(token: str) -> str:
    if token in DOMAIN_LEMMAS:
        return DOMAIN_LEMMAS[token]
    if token in PROTECTED_TERMS or any(char.isdigit() for char in token) or "-" in token:
        return token
    return lemmatize(token, lang="en", greedy=False).lower()


def _trend_tokens(text: str) -> list[str]:
    value = _URL.sub(" ", text.lower().replace("\u2019", "'"))
    value = re.sub(r"\bdota[ \t-]+2\b", "dota2", value)
    value = re.sub(r"\b(?:anti[ \t_-]*cheat|anit-cheat)\b", "anticheat", value)
    value = re.sub(r"\b(64|128)[ \t-]*tick\b", r"\1-tick", value)
    value = re.sub(r"\b\d+(?:\.\d+)?(hours?|hrs?|minutes?|mins?|seconds?|secs?|euros?|dollars?|kills?|deaths?)\b", r" \1", value)
    value = re.sub(r"\b\d+(?:\.\d+)?k\b", " ", value)
    value = re.sub(r"\bcs[ \t:-]+(2|go)\b", r"cs\1", value)
    for contraction, expansion in _NEGATIVE_CONTRACTIONS.items():
        value = re.sub(r"\b" + re.escape(contraction) + r"\b", expansion, value)
    split_pattern = r"\b(" + "|".join(_SPLIT_NEGATIVES) + r")[ \t]+t\b"
    value = re.sub(split_pattern, lambda match: _SPLIT_NEGATIVES[match[1]] + " not", value)
    value = re.sub(r"n\'t\b", " not", value)
    value = re.sub(r"\b(i|you|we|they|he|she|it|that|there|here)'(?:m|re|ve|ll|d|s)\b", r"\1", value)
    value = re.sub(r"(?<!\w)\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?(?!\w)", " ", value)
    value = re.sub(r"(?<!\w)\d+(?:\.\d+)?\s*%", " ", value)
    contextual_versions = set(re.findall(r"\b(?:version|ver|patch|update)[ \t]+(\d+(?:\.\d+)+)\b", value))
    tokens = []
    for token in _word_tokens(value):
        if token.endswith("'s"):
            token = token[:-2]
        if not token or token in STOP_WORDS:
            continue
        if not any(char.isalpha() for char in token):
            if not (token == "1.6" or token.count(".") >= 2 or token in contextual_versions):
                continue
        if any(char.isalpha() and "LATIN" not in unicodedata.name(char, "") for char in token):
            continue
        lemma = _lemma(token)
        if lemma not in STOP_WORDS:
            tokens.append(lemma)
    return tokens


@lru_cache(maxsize=2048)
def _detect(text: str) -> tuple[str, float]:
    try:
        results = detect_langs(text)
        return (results[0].lang, results[0].prob) if results else ("", 0.0)
    except LangDetectException:
        return "", 0.0


@lru_cache(maxsize=2048)
def _language_quality(text: str, api_language: str) -> tuple[str, tuple[str, ...], bool, bool]:
    if api_language.lower() not in {"english", "en"}:
        return "rejected", ("api_language_not_english",), False, False
    letters = [char for char in text if char.isalpha()]
    if not letters:
        return "rejected", ("no_textual_content",), False, False
    words = [word for word in _word_tokens(_URL.sub(" ", text)) if any(char.isalpha() for char in word)]
    latin_words = [word for word in words if all(not char.isalpha() or "LATIN" in unicodedata.name(char, "") for char in word)]
    non_latin = sum("LATIN" not in unicodedata.name(char, "") for char in letters)
    foreign_fraction = non_latin / len(letters)
    language, probability = _detect(text)
    known_count = sum(_known_english(word) for word in latin_words)
    known_fraction = known_count / max(len(latin_words), 1)
    strong_clues = sum((word in STOP_WORDS or word in _SHORT_ENGLISH_WORDS)
                       and word not in _WEAK_BORROWINGS for word in latin_words)
    foreign_hits = sum(word in _FOREIGN_CUES for word in latin_words)
    flags = []
    if non_latin >= 2:
        flags.append("mixed_language_suspected")
        if foreign_fraction > 0.5:
            return "rejected", tuple(flags + ["non_english_text"]), False, False
        reliable_english = (len(latin_words) >= 12 and strong_clues >= 3
                            and known_fraction >= 0.55 and language == "en" and probability >= 0.8)
        foreign_positions = [i for i, char in enumerate(text)
                             if char.isalpha() and "LATIN" not in unicodedata.name(char, "")]
        # A tiny foreign fraction alone does not establish harmlessness. Require
        # an explicit name context or a following English parenthetical gloss.
        first_foreign, last_foreign = foreign_positions[0], foreign_positions[-1]
        name_context = bool(
            re.match(r"\s*\([A-Za-z][A-Za-z -]{2,50}\)", text[last_foreign + 1:])
            or re.search(r"\b(?:named|titled|called)\s*[:\"']?\s*$", text[max(0, first_foreign - 60):first_foreign], re.I))
        minor_foreign_name = (reliable_english and foreign_fraction <= 0.04
                              and len(latin_words) >= 40 and name_context)
        if minor_foreign_name:
            return "uncertain", tuple(flags), True, True
        return "uncertain", tuple(flags + ["mixed_language_sentiment_excluded"]), reliable_english, False
    if not latin_words:
        return "rejected", ("non_english_text",), False, False
    all_known = known_count == len(latin_words)
    if len(latin_words) == 1 and not all_known:
        return "uncertain", ("short_or_domain_text", "unclear_semantics"), False, False
    if language != "en" and probability >= 0.9 and known_fraction < 0.4 and len(latin_words) >= 2:
        return "rejected", ("non_english_text",), False, False
    if foreign_hits and language != "en" and probability >= 0.8:
        if strong_clues == 0 or foreign_hits / len(latin_words) >= 0.4:
            return "rejected", ("non_english_text",), False, False
        return "uncertain", ("mixed_language_suspected", "mixed_language_sentiment_excluded"), False, False
    if all_known and (strong_clues or all(word in PROTECTED_TERMS for word in latin_words)):
        if len(latin_words) < 5:
            return "uncertain", ("short_or_domain_text",), True, True
        return "passed", (), True, True
    if language == "en" and probability >= 0.8:
        return "passed", (), True, True
    if len(latin_words) < 5 and known_fraction < 0.5:
        return "uncertain", ("short_or_domain_text", "unclear_semantics"), False, False
    return "uncertain", ("language_detection_uncertain",), True, True


def clean_review_text(text: str, api_language: str = "english") -> dict:
    """Return immutable raw text, analysis views and explicit eligibility.

    Mixed-language reviews are never silently reduced to an English fragment.
    Substantial English with only a very small foreign fragment remains eligible
    for English sentiment; all original evidence is always preserved.
    """
    if not isinstance(text, str):
        raise TypeError("review text must be a string")
    cleaned, format_flags = _light_clean(text)
    language_check, language_flags, trend_allowed, sentiment_allowed = _language_quality(cleaned, api_language)
    flags = format_flags + list(language_flags)
    if not cleaned:
        flags.append("empty_text")
    vote_bait = bool(re.search(r"\b(?:give|leave|hit|press).{0,35}\bthumbs?[ -]up\b|\b(?:upvote|award me)\b", cleaned, re.I))
    if vote_bait:
        flags.append("vote_farming_suspected")
        topic_words = set(_word_tokens(cleaned)) & (PROTECTED_TERMS - {"no", "not", "never", "without", "better", "best", "worse", "worst"})
        if "ascii_art_removed" in flags and not topic_words:
            flags.append("low_relevance_suspected")
            trend_allowed = sentiment_allowed = False
    tokens = _trend_tokens(cleaned) if trend_allowed else []
    if not tokens:
        flags.append("no_trend_tokens")
    return {
        "text_raw": text, "text_clean": cleaned, "tokens_trend": tokens,
        "language_check": language_check, "quality_flags": list(dict.fromkeys(flags)),
        "usable_for_trend": bool(cleaned) and trend_allowed and bool(tokens),
        "usable_for_sentiment": bool(cleaned) and sentiment_allowed,
        "pipeline_version": PIPELINE_VERSION,
    }
