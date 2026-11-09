
"""Keyword frequency statistics for cleaned Steam reviews."""

from collections import Counter


def calculate_frequency(reviews, top_n=None):
    """
    Calculate TF and DF from current-version review records.

    Args:
        reviews: An iterable of review dictionaries.
        top_n: Maximum number of returned keywords.
               None returns all keywords.

    Returns:
        A dictionary containing total_reviews and keywords.
    """
    if top_n is not None and (
        isinstance(top_n, bool)
        or not isinstance(top_n, int)
        or top_n < 1
    ):
        raise ValueError("top_n must be a positive integer or None")

    tf_counter = Counter()
    df_counter = Counter()
    total_reviews = 0

    for review in reviews:
        if not review.get("usable_for_trend", False):
            continue

        tokens = review.get("tokens_trend")

        if not isinstance(tokens, list):
            raise ValueError("Usable review must contain tokens_trend list")

        if any(not isinstance(token, str) for token in tokens):
            raise ValueError("tokens_trend must contain strings")

        total_reviews += 1

        # TF: count every occurrence of each keyword.
        tf_counter.update(tokens)

        # DF: count a keyword at most once per review.
        df_counter.update(set(tokens))

    keywords = []

    for term, tf in tf_counter.items():
        df = df_counter[term]

        keywords.append({
            "term": term,
            "tf": tf,
            "df": df,
            "document_rate": df / total_reviews,
        })

    # Deterministic ranking: DF, then TF, then alphabetically.
    keywords.sort(
        key=lambda item: (
            -item["df"],
            -item["tf"],
            item["term"],
        )
    )

    if top_n is not None:
        keywords = keywords[:top_n]

    return {
        "total_reviews": total_reviews,
        "keywords": keywords,
    }
