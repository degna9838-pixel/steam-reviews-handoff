from base64 import b64encode
from datetime import timedelta
from html import escape
from json import dumps
from pathlib import Path

import altair as alt
import streamlit as st

from demo_data import GAMES, SENTIMENTS, SNAPSHOT_AT, TIME_RANGES, demo_snapshot, sentiment_alert


HERE = Path(__file__).resolve().parent
COLORS = {"Negative": "#f07880", "Neutral": "#a5b7cc", "Positive": "#79ce83"}

st.set_page_config(page_title="Steam Review Monitor", page_icon="📊", layout="wide")
st.html(HERE / "styles.css")


def cover_html(game_id, game):
    cover = HERE / "assets" / f"{game_id}.jpg"
    if cover.is_file():
        data = b64encode(cover.read_bytes()).decode("ascii")
        return f'<img class="game-cover" src="data:image/jpeg;base64,{data}" alt="{escape(game["name"])} cover">'
    return f'<div class="game-cover cover-placeholder">{escape(game["short"])}</div>'


def sentiment_chart(buckets, hours, alert):
    start = SNAPSHOT_AT - timedelta(hours=hours)
    # Each point is the end of an hourly bucket, with an anchor at the left edge.
    chart_points = [buckets[0], *buckets]
    rows = [
        {
            "Hour": hour,
            "Window end": bucket["end"].strftime("%b %d · %H:%M UTC+8"),
            "Sentiment": sentiment,
            "Order": order,
            "Share": count / sum(bucket["counts"]),
            "Reviews": count,
        }
        for hour, bucket in enumerate(chart_points)
        for order, (sentiment, count) in enumerate(zip(SENTIMENTS, bucket["counts"]))
    ]
    # Plot elapsed hours and format labels in the snapshot's fixed UTC+8 zone.
    tick_step = 24 if hours > 24 else (4 if hours > 6 else 1)
    ticks = list(range(0, hours + 1, tick_step))
    time_format = "%b %d" if hours > 24 else "%H:%M"
    labels = [(start + timedelta(hours=tick)).strftime(time_format) for tick in ticks]
    x = alt.X(
        "Hour:Q",
        scale=alt.Scale(domain=[0, hours], nice=False),
        axis=alt.Axis(
            title=None,
            values=ticks,
            labelExpr=f"{dumps(labels)}[datum.value / {tick_step}]",
        ),
    )
    area = alt.Chart(alt.InlineData(values=rows)).mark_area(
        opacity=0.88, interpolate="step-before", clip=True
    ).encode(
        x=x,
        y=alt.Y(
            "Share:Q", stack="zero", title=None, scale=alt.Scale(domain=[0, 1]),
            axis=alt.Axis(format=".0%", values=[0, 0.25, 0.5, 0.75, 1]),
        ),
        color=alt.Color(
            "Sentiment:N",
            scale=alt.Scale(domain=list(SENTIMENTS), range=[COLORS[s] for s in SENTIMENTS]),
            legend=alt.Legend(title=None, orient="top", direction="horizontal"),
        ),
        order=alt.Order("Order:Q", sort="ascending"),
        tooltip=["Window end:N", "Sentiment:N", alt.Tooltip("Share:Q", format=".0%"), "Reviews:Q"],
    )
    chart = area
    if alert:
        event = alt.Chart(alt.InlineData(values=[
            {"Hour": hours, "Share": alert[0], "Change": f"+{alert[2] * 100:.0f} pp"}
        ]))
        guide = event.mark_rule(color="#f3a8ad", strokeDash=[4, 3]).encode(x=x)
        dot = event.mark_point(color="#f07880", filled=True, stroke="#fff", size=70).encode(x=x, y="Share:Q")
        label = event.mark_text(
            align="right", dx=-8, dy=-10, color="#fff", fontWeight="bold", fontSize=12
        ).encode(x=x, y="Share:Q", text="Change:N")
        chart = area + guide + dot + label

    return (
        chart.properties(height=200, background="transparent")
        .configure_axis(
            labelColor="#bacadd", domainColor="#637c94", tickColor="#637c94",
            gridColor="#46617a", gridOpacity=0.45, labelFontSize=11, labelPadding=5,
        )
        .configure_legend(labelColor="#e5edf5", labelFontSize=12, symbolType="square")
        .configure_view(stroke=None)
    )


def breakdown_html(counts):
    total = sum(counts)
    parts, rows = [], []
    for sentiment, count in reversed(list(zip(SENTIMENTS, counts))):
        color = COLORS[sentiment]
        rate = count / total
        parts.append(f'<span style="background:{color};width:{rate:.5%}"></span>')
        rows.append(
            f'<div class="breakdown-row"><span><i style="background:{color}"></i>{sentiment}</span>'
            f'<strong>{rate:.0%}</strong></div>'
        )
    return (
        f'<p class="panel-note">{total:,} classified reviews</p>'
        f'<div class="breakdown-bar" aria-hidden="true">{"".join(parts)}</div>{"".join(rows)}'
    )


def terms_html(terms):
    rows = "".join(
        f'<tr><th scope="row">{escape(word)}</th><td>{recent}%</td><td>{base}%</td>'
        f'<td class="lift">{lift:.2f}×</td></tr>'
        for word, recent, base, lift in terms
    )
    return f"""
        <p class="panel-note">Last hour vs preceding 24 hours</p>
        <div class="table-frame"><table class="term-table">
            <thead><tr><th>Keyword</th><th>Recent</th><th>Baseline</th><th>Lift</th></tr></thead>
            <tbody>{rows}</tbody>
        </table></div>
        <p class="panel-note table-note">% of valid reviews mentioning the term</p>
    """


def render_review_samples(game):
    examples = game["examples"]
    start = SNAPSHOT_AT - timedelta(hours=1)
    st.html(f"""
        <p class="panel-note">Last hour · {start:%b %d · %H:%M}–{SNAPSHOT_AT:%H:%M}
            (UTC+8) · {len(examples)} demo examples</p>
    """)
    for minutes_before, review, sentiment, recommended in examples:
        timestamp = SNAPSHOT_AT - timedelta(minutes=minutes_before)
        st.html(f"""
            <article class="review">
                <div class="review-line"><p>“{escape(review)}”</p><time>{timestamp:%H:%M}</time></div>
                <div class="review-tags">
                    <span class="tag {sentiment.lower()}">Text sentiment: {sentiment}</span>
                    <span class="tag">Steam: {"Recommended" if recommended else "Not recommended"}</span>
                </div>
            </article>
        """)


@st.dialog("Related reviews", width="medium")
def show_related_reviews(game):
    st.subheader(game["name"])
    render_review_samples(game)


st.html("""
    <header class="app-header">
        <div class="brand"><h1>Steam Review Monitor</h1><span class="demo-badge">DEMO DATA</span></div>
        <div class="status">Last collection 20:00 · Analysis updated 20:02
            <span>Nov 9, 2026 (UTC+8) · Auto-refresh off</span>
        </div>
    </header>
""")

game_id = st.session_state.get("game", 730)
game = GAMES[game_id]
st.html(f"""
    <section class="game-heading">
        {cover_html(game_id, game)}
        <div><h2>{escape(game["name"])}</h2><p>Steam English reviews</p></div>
    </section>
""")

game_filter, time_filter = st.columns(2)
with game_filter:
    st.selectbox("Game", options=list(GAMES), format_func=lambda item: GAMES[item]["name"], key="game")
with time_filter:
    period = st.selectbox("Time range", options=list(TIME_RANGES), index=1)

hours = TIME_RANGES[period]
buckets, terms = demo_snapshot(game_id)
shown_buckets = buckets[-hours:]
counts = tuple(map(sum, zip(*(bucket["counts"] for bucket in shown_buckets))))
reviews = sum(bucket["reviews"] for bucket in shown_buckets)
alert = sentiment_alert(buckets)
game_name = escape(game["short"])

for column, (label, value, note) in zip(st.columns(4), [
    (f"{game_name} reviews", f"{reviews:,}", f"{period} · all reviews"),
    (f"{game_name} negative", f"{counts[0] / sum(counts):.0%}", f"{counts[0]:,} of {sum(counts):,} classified"),
    ("Trending terms", str(len(terms)), f"{game_name} · last hour"),
    (f"{game_name} alerts", str(int(alert is not None)), "Latest detection window"),
]):
    with column:
        st.html(f"""
            <div class="metric-card">
                <div class="metric-label">{label}</div><strong>{value}</strong><small>{note}</small>
            </div>
        """)

analysis_panels = st.container(key="analysis_panels")
main, side = analysis_panels.columns([2, 1], border=True)
with main:
    st.html(f"<h2>{game_name} · Sentiment over time</h2>")
    st.altair_chart(sentiment_chart(shown_buckets, hours, alert), theme=None, width="stretch")
    st.html(f'<p class="chart-note">{period} · Hourly buckets · UTC+8</p>')
with side:
    st.html(f"<h2>{game_name} · Sentiment breakdown</h2>{breakdown_html(counts)}")

main, side = analysis_panels.columns([2, 1], border=True)
with main:
    st.html(f"<h2>{game_name} · Trending terms</h2>{terms_html(terms)}")
with side:
    st.html(f"<h2>{game_name} · Sentiment alert</h2>")
    if alert:
        recent, baseline, change, recent_count, baseline_count = alert
        st.html(f"""
            <div class="alert-card">
                <strong>Negative sentiment up {change * 100:.0f} pp</strong>
                <span>{recent:.0%} vs {baseline:.0%}</span>
            </div>
            <p class="panel-note alert-note">Last hour ({recent_count}) vs preceding 24h ({baseline_count})
                <br>{SNAPSHOT_AT:%b %d · %H:%M} (UTC+8)</p>
        """)
        if st.button("View related reviews", key="view_related_reviews"):
            show_related_reviews(game)
    else:
        st.html('<p class="empty-alert">No alert in the latest window.</p>')

with st.container(border=True, key="related_reviews"):
    st.html(f"<h2>{game_name} · Recent review samples</h2>")
    render_review_samples(game)
