from base64 import b64encode
from datetime import datetime, timedelta, timezone
from html import escape
from json import dumps
from pathlib import Path

# Import pandas before Altair.  With Python 3.12, Altair's optional pandas
# adapter can otherwise observe pandas while it is still being initialized.
import pandas
import altair as alt
import streamlit as st
from wordcloud import WordCloud

from data_source import DashboardAPIError, get_games, get_overview


HERE = Path(__file__).resolve().parent
COLORS = {"Negative": "#f07880", "Neutral": "#a5b7cc", "Positive": "#79ce83"}
SENTIMENTS = tuple(COLORS)
TIME_RANGES = {"Last 6 hours": 6, "Last 24 hours": 24, "Last 7 days": 168}
# Presentation labels only; enabled games and all statistics come from the API.
GAME_LABELS = {
    730: {"name": "Counter-Strike 2", "short": "CS2"},
    570: {"name": "Dota 2", "short": "Dota 2"},
    4115450: {"name": "Phantom Blade Zero", "short": "Phantom Blade Zero"},
}
DISPLAY_TZ = timezone(timedelta(hours=8))
TREND_MESSAGES = {
    "pending": "Trending terms are pending analysis.",
    "insufficient_data": "Not enough reviews to detect trending terms.",
}
ALERT_MESSAGES = {
    "pending": "Sentiment alert analysis is pending.",
    "insufficient_data": "Not enough classified reviews to check for an alert.",
    "clear": "No alert in the latest window.",
}

st.set_page_config(page_title="Steam Review Monitor", page_icon="📊", layout="wide")
st.html(HERE / "styles.css")


def cover_html(game_id, game):
    cover = HERE / "assets" / f"{game_id}.jpg"
    if cover.is_file():
        data = b64encode(cover.read_bytes()).decode("ascii")
        return f'<img class="game-cover" src="data:image/jpeg;base64,{data}" alt="{escape(game["name"])} cover">'
    return f'<div class="game-cover cover-placeholder">{escape(game["short"])}</div>'


def local_time(value):
    return datetime.fromisoformat(value).astimezone(DISPLAY_TZ)


def sentiment_chart(buckets, start, alert):
    hours = len(buckets)
    rows = []
    hover_rows = []
    segment = None
    for hour, bucket in enumerate(buckets, start=1):
        counts = bucket["counts"]
        classified = counts["classified_count"] if counts else 0
        window_end = local_time(bucket["end_at"]).strftime("%b %d · %H:%M UTC+8")
        hover_rows.append({
            "Hour": hour,
            "Hover start": 0 if hour == 1 else hour - 0.5,
            "Hover end": min(hour + 0.5, hours),
            "Window end": window_end,
            **{
                sentiment: f"{counts[sentiment.lower() + '_count'] / classified:.1%}" if classified else "—"
                for sentiment in SENTIMENTS
            },
        })
        if not classified:
            segment = None
            continue
        # Anchor each uninterrupted run at its left edge and leave gaps between runs.
        positions = (hour,)
        if segment is None:
            segment = hour
            positions = (hour - 1, hour)
        for position in positions:
            cumulative = 0
            for sentiment in SENTIMENTS:
                count = counts[f"{sentiment.lower()}_count"]
                lower = cumulative / classified
                cumulative += count
                rows.append({
                    "Hour": position,
                    "Sentiment": sentiment,
                    "Segment": segment,
                    "Lower": lower,
                    "Upper": cumulative / classified,
                })
    if not rows:
        return None
    # Plot elapsed hours and format labels in the snapshot's fixed UTC+8 zone.
    tick_step = 24 if hours > 24 else (4 if hours > 6 else 1)
    ticks = list(range(0, hours + 1, tick_step))
    time_format = "%b %d" if hours > 24 else "%H:%M"
    labels = [(start + timedelta(hours=tick)).strftime(time_format) for tick in ticks]
    x_scale = alt.Scale(domain=[0, hours], nice=False)
    x_axis = alt.Axis(
        title=None,
        values=ticks,
        labelExpr=f"{dumps(labels)}[datum.value / {tick_step}]",
    )
    x = alt.X("Hour:Q", scale=x_scale, axis=x_axis)
    # Use explicit band boundaries so Vega-Lite does not fill gaps with stacked values.
    area = alt.Chart(alt.InlineData(values=rows)).mark_area(
        opacity=0.88, interpolate="monotone", clip=True
    ).encode(
        x=x,
        y=alt.Y(
            "Upper:Q", title=None, scale=alt.Scale(domain=[0, 1]),
            axis=alt.Axis(format=".0%", values=[0, 0.25, 0.5, 0.75, 1]),
        ),
        y2="Lower:Q",
        color=alt.Color(
            "Sentiment:N",
            scale=alt.Scale(domain=list(SENTIMENTS), range=[COLORS[s] for s in SENTIMENTS]),
            legend=alt.Legend(title=None, orient="top", direction="horizontal"),
        ),
        detail="Segment:N",
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

    hover = alt.selection_point(
        name="hover_hour", fields=["Hour"], on="pointerover", clear="pointerout", toggle=False, empty=False,
    )
    hover_data = alt.Chart(alt.InlineData(values=hover_rows))
    crosshair = hover_data.mark_rule(color="#bacadd", opacity=0.75).encode(x=x).transform_filter(hover)
    # Full-height hit areas keep the tooltip tied to the hour, wherever the pointer is vertically.
    hit_areas = hover_data.mark_rect(fill="transparent").encode(
        x=alt.X("Hover start:Q", scale=x_scale, axis=x_axis),
        x2="Hover end:Q",
        y=alt.value(0),
        y2=alt.value("height"),
        tooltip=[
            alt.Tooltip("Window end:N", title="Hour ending"),
            *[alt.Tooltip(f"{sentiment}:N") for sentiment in SENTIMENTS],
        ],
    ).add_params(hover)

    return (
        (chart + crosshair + hit_areas).properties(height=200, background="transparent")
        .configure_axis(
            labelColor="#bacadd", domainColor="#637c94", tickColor="#637c94",
            gridColor="#46617a", gridOpacity=0.45, labelFontSize=11, labelPadding=5,
        )
        .configure_legend(labelColor="#e5edf5", labelFontSize=12, symbolType="square")
        .configure_view(stroke=None)
    )


def render_breakdown(counts):
    if counts is None:
        st.html('<p class="panel-note">Sentiment totals are unavailable for this range.</p>')
        return
    total = sum(counts)
    if not total:
        st.html('<p class="panel-note">No classified reviews in this range.</p>')
        return

    legend = list(reversed(list(zip(SENTIMENTS, counts))))
    slices = [
        {"Sentiment": sentiment, "Reviews": count, "Share": count / total, "Order": order}
        for order, (sentiment, count) in enumerate(legend)
        if count
    ]
    chart = (
        alt.Chart(alt.InlineData(values=slices))
        .mark_arc(
            outerRadius=alt.ExprRef(expr="max(0, min(width, height) / 2 - 6)"),
            stroke="#23384b",
            strokeWidth=1,
        )
        .encode(
            theta=alt.Theta("Reviews:Q", stack=True),
            color=alt.Color(
                "Sentiment:N",
                scale=alt.Scale(domain=list(SENTIMENTS), range=[COLORS[s] for s in SENTIMENTS]),
                legend=None,
            ),
            order=alt.Order("Order:Q", sort="ascending"),
            tooltip=["Sentiment:N", "Reviews:Q", alt.Tooltip("Share:Q", format=".0%")],
        )
        .properties(
            height=144,
            padding={"top": 8, "right": 8, "bottom": 8, "left": 8},
            background="transparent",
        )
        .configure_view(stroke=None)
    )
    st.html(f'<p class="panel-note">{total:,} classified reviews</p>')
    st.altair_chart(chart, theme=None, width="stretch")
    st.html("".join(
        f'<div class="breakdown-row"><span><i style="background:{COLORS[sentiment]}"></i>{sentiment}</span>'
        f'<strong>{count / total:.0%}</strong></div>'
        for sentiment, count in legend
    ))


def render_term_cloud(keywords):
    if keywords["status"] != "ready":
        st.html('<p class="panel-note">Keyword statistics are pending for this range.</p>')
        return
    if not keywords["eligible_count"]:
        st.html('<p class="panel-note">No eligible reviews in this range.</p>')
        return
    frequencies = {
        item["term"]: item["review_count"]
        for item in keywords["items"] if item["review_count"] > 0
    }
    if not frequencies:
        st.html('<p class="panel-note">No usable keywords in this range.</p>')
        return

    palette = ("#66c0f4", "#93d8ff", "#79ce83", "#bacadd")
    colors = {word: palette[index % len(palette)] for index, word in enumerate(frequencies)}
    cloud = WordCloud(
        width=1440,
        height=340,
        background_color="#23384b",
        prefer_horizontal=1.0,
        max_words=50,
        max_font_size=100,
        min_font_size=12,
        relative_scaling=0.5,
        margin=5,
        random_state=42,
        color_func=lambda word, **kwargs: colors[word],
    ).generate_from_frequencies(frequencies)
    st.image(cloud.to_array(), width="stretch")


def terms_html(trends, candidates, keywords, period):
    state = trends["status"]
    items = trends["items"] or candidates
    message = ""
    if not trends["items"]:
        message = TREND_MESSAGES.get(state, "No surging terms detected.")
    recent_total = trends["recent_eligible_count"]
    baseline_total = trends["baseline_eligible_count"]
    if not items or not recent_total or not baseline_total:
        if keywords["status"] == "ready" and keywords["eligible_count"] and keywords["items"]:
            rows = "".join(
                f'<tr><th scope="row">{escape(item["term"])}</th><td>{item["review_count"]:,}</td>'
                f'<td>{item["review_count"] / keywords["eligible_count"]:.0%}</td></tr>'
                for item in keywords["items"][:10]
            )
            return f'''<p class="panel-note">{message} Showing frequent keywords · {period}.</p>
                <div class="table-frame"><table class="term-table">
                <thead><tr><th>Keyword</th><th>Reviews</th><th>Share</th></tr></thead>
                <tbody>{rows}</tbody></table></div>'''
        return f'<p class="panel-note">{message or "No keyword activity in this window."}</p>'

    rows = []
    for item in items:
        recent = item["recent_review_count"] / recent_total
        baseline = item["baseline_review_count"] / baseline_total
        lift = f"{recent / baseline:.2f}×" if baseline else ("New" if recent else "—")
        rows.append(
            f'<tr><th scope="row">{escape(item["term"])}</th><td>{recent:.0%}</td><td>{baseline:.0%}</td>'
            f'<td class="lift">{lift}</td></tr>'
        )
    return f"""
        <p class="panel-note">{message + ' Showing frequent terms, not confirmed surges.' if message else 'Detected surging terms.'}</p>
        <p class="panel-note">Last hour vs preceding 24 hours</p>
        <div class="table-frame"><table class="term-table">
            <thead><tr><th>Keyword</th><th>Recent</th><th>Baseline</th><th>Lift</th></tr></thead>
            <tbody>{"".join(rows)}</tbody>
        </table></div>
        <p class="panel-note table-note">% of valid reviews mentioning the term</p>
    """


def render_review_samples(snapshot):
    examples = snapshot["review_samples"]
    window = snapshot["comparison"]["recent"]
    start, end = local_time(window["start_at"]), local_time(window["end_at"])
    end_format = "%H:%M" if start.date() == end.date() else "%b %d · %H:%M"
    label = "demo examples" if snapshot["status"]["mode"] == "demo" else "review samples"
    st.html(f"""
        <p class="panel-note">Last hour · {start:%b %d · %H:%M}–{end:{end_format}}
            (UTC+8) · {len(examples)} {label}</p>
    """)
    for example in examples:
        timestamp = local_time(example["created_at"])
        sentiment = (example["sentiment"] or "Unclassified").capitalize()
        recommendation = "Recommended" if example["steam_recommended"] else "Not recommended"
        st.html(f"""
            <article class="review">
                <div class="review-line"><p>“{escape(example["text_raw"])}”</p><time>{timestamp:%H:%M}</time></div>
                <div class="review-tags">
                    <span class="tag {escape(sentiment.lower())}">Text sentiment: {escape(sentiment)}</span>
                    <span class="tag">Steam: {recommendation}</span>
                </div>
            </article>
        """)


@st.dialog("Related reviews", width="medium")
def show_related_reviews(game, snapshot):
    st.subheader(game["name"])
    render_review_samples(snapshot)


@st.fragment
def review_button(game, snapshot):
    if st.button("View related reviews", key="view_related_reviews"):
        show_related_reviews(game, snapshot)


def header_html(snapshot, fetched_at):
    status = snapshot["status"]
    as_of = local_time(snapshot["as_of"])
    collection = status["last_collection_at"]
    updated = status["analysis_updated_at"]
    collection_text = f"{local_time(collection):%b %d %H:%M}" if collection else "—"
    updated_text = f"{local_time(updated):%b %d %H:%M}" if updated else "—"
    state = status["data_state"]
    state_note = {"partial": " · Partial data", "pending": " · Awaiting data"}.get(state, "")
    refreshed_text = f" · Last refresh {fetched_at.astimezone(DISPLAY_TZ):%H:%M:%S}" if fetched_at else ""
    return f"""
        <header class="app-header">
            <div class="brand"><h1>Steam Review Monitor</h1></div>
            <div class="status">Last collection {collection_text} · Analysis updated {updated_text}
                <span>{as_of:%b %d, %Y} (UTC+8) · Auto-refresh off{state_note}{refreshed_text}</span>
            </div>
        </header>
    """


def alert_rates(alert):
    if alert["status"] != "triggered":
        return None
    recent, baseline = alert["recent"], alert["baseline"]
    if not recent or not baseline or not recent["classified_count"] or not baseline["classified_count"]:
        return None
    recent_rate = recent["negative_count"] / recent["classified_count"]
    baseline_rate = baseline["negative_count"] / baseline["classified_count"]
    return recent_rate, baseline_rate, recent_rate - baseline_rate


with st.container(key="dashboard_topbar"):
    title_column, refresh_column = st.columns([8, 1.3], vertical_alignment="center")
    header = title_column.empty()
    refresh_requested = refresh_column.button(
        "立刻刷新", icon=":material/refresh:", key="refresh_dashboard", width="stretch",
        help="重新获取所选游戏和时间范围的最新后端数据。",
    )

try:
    directory = get_games()
except DashboardAPIError as exc:
    st.error(f"Could not load the game directory. {exc}")
    st.stop()

games = {
    item["app_id"]: GAME_LABELS.get(item["app_id"], {"name": item["name"], "short": item["name"]})
    for item in directory["games"] if item["enabled"]
}
if not games:
    st.info("No games are enabled in the collector.")
    st.stop()

heading = st.empty()
game_filter, time_filter = st.columns(2)
with game_filter:
    if st.session_state.get("game") not in games:
        st.session_state.pop("game", None)
    game_id = st.selectbox("Game", options=list(games), format_func=lambda item: games[item]["name"], key="game")
with time_filter:
    period = st.selectbox("Time range", options=list(TIME_RANGES), index=1)

game = games[game_id]
heading.html(f"""
    <section class="game-heading">
        {cover_html(game_id, game)}
        <div><h2>{escape(game["name"])}</h2><p>Steam English reviews</p></div>
    </section>
""")

hours = TIME_RANGES[period]
snapshot_key = (game_id, hours)
snapshots = st.session_state.setdefault("overview_snapshots", {})
fetched_times = st.session_state.setdefault("overview_fetched_times", {})
try:
    with st.spinner("正在获取最新数据…", show_time=True):
        snapshot = get_overview(game_id, hours)
except DashboardAPIError as exc:
    snapshot = snapshots.get(snapshot_key)
    if snapshot is None:
        st.error(f"Could not load the dashboard overview. {exc}")
        st.stop()
    st.warning(f"Showing the last successful snapshot from {local_time(snapshot['as_of']):%b %d · %H:%M} (UTC+8). {exc}")
else:
    snapshots[snapshot_key] = snapshot
    fetched_times[snapshot_key] = datetime.now(timezone.utc)
    if refresh_requested:
        st.toast("已获取后端最新数据。", icon=":material/check_circle:")

header.html(header_html(snapshot, fetched_times.get(snapshot_key)))
if snapshot["status"].get("sync_in_progress"):
    st.info(
        f"Syncing new data ({snapshot['status']['cursor_lag']:,} changes pending). "
        "Charts show processed data; statistics are temporarily incomplete. "
        "Trending terms and alerts will update once syncing is complete."
    )
pending = snapshot["status"]["data_state"] == "pending"
summary = None if pending else snapshot["summary"]
counts = tuple(summary[f"{label.lower()}_count"] for label in SENTIMENTS) if summary is not None else None
classified = summary["classified_count"] if summary is not None else 0
negative_rate = f"{counts[0] / classified:.0%}" if classified else "—"
negative_note = f"{counts[0]:,} of {classified:,} classified" if summary is not None else "Totals unavailable"
reviews = f"{summary['review_count']:,}" if summary is not None else "—"
trends = snapshot["trends"]
keywords = snapshot.get("keywords", {"status": "pending", "eligible_count": None, "items": []})
term_count = str(len(trends["items"])) if trends["status"] == "ready" else (
    "Low sample" if trends["status"] == "insufficient_data" else "Pending")
alert = snapshot["alert"]
rates = alert_rates(alert)
alert_count = {"triggered": "1", "clear": "0", "insufficient_data": "Low sample"}.get(alert["status"], "Pending")
alert_note = "Latest detection window"
if alert["status"] == "insufficient_data" and alert["recent"] and alert["baseline"]:
    alert_note = (f'{alert["recent"]["classified_count"]}/{alert["min_classified_per_window"]} recent · '
                  f'{alert["baseline"]["classified_count"]}/{alert["min_classified_per_window"]} baseline')
game_name = escape(game["short"])

for column, (label, value, note) in zip(st.columns(4), [
    (f"{game_name} reviews", reviews, f"{period} · all reviews"),
    (f"{game_name} negative", negative_rate, negative_note),
    ("Trending terms", term_count, f"{game_name} · last hour"),
    (f"{game_name} alerts", alert_count, alert_note),
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
    chart = None if pending else sentiment_chart(snapshot["buckets"], local_time(snapshot["range"]["start_at"]), rates)
    with st.container(key="sentiment_trend"):
        if chart is None:
            st.html('<p class="panel-note">No hourly sentiment data available for this range.</p>')
        else:
            st.altair_chart(chart, theme=None, width="stretch")
        st.html(f'<p class="chart-note">{period} · Hourly buckets · UTC+8</p>')
with side:
    st.html(f"<h2>{game_name} · Sentiment breakdown</h2>")
    render_breakdown(counts)

with analysis_panels.container(border=True, key="term_cloud"):
    st.html(f"<h2>{game_name} · Word cloud</h2>")
    st.html(f'<p class="panel-note">Frequent keywords · {period} · Word size reflects review mentions</p>')
    render_term_cloud(keywords)

main, side = analysis_panels.columns([2, 1], border=True)
with main:
    st.html(f"<h2>{game_name} · Trending terms</h2>")
    st.html(terms_html(trends, snapshot.get("trend_candidates", []), keywords, period))
with side:
    st.html(f"<h2>{game_name} · Sentiment alert</h2>")
    if rates:
        recent, baseline, change = rates
        recent_count = alert["recent"]["classified_count"]
        baseline_count = alert["baseline"]["classified_count"]
        event_at = local_time(snapshot["comparison"]["recent"]["end_at"])
        st.html(f"""
            <div class="alert-card">
                <strong>Negative sentiment up {change * 100:.0f} pp</strong>
                <span>{recent:.0%} vs {baseline:.0%}</span>
            </div>
            <p class="panel-note alert-note">Last hour ({recent_count}) vs preceding 24h ({baseline_count})
                <br>{event_at:%b %d · %H:%M} (UTC+8)</p>
        """)
        review_button(game, snapshot)
    else:
        message = ALERT_MESSAGES.get(alert["status"], "Alert counts are unavailable.")
        st.html(f'<p class="empty-alert">{message}</p>')
        recent, baseline = alert["recent"], alert["baseline"]
        if recent is not None and baseline is not None:
            minimum = alert["min_classified_per_window"]
            for label, values in (("Last hour", recent), ("Preceding 24 hours", baseline)):
                total, negative = values["classified_count"], values["negative_count"]
                share = f"{negative / total:.1%}" if total else "—"
                st.html(f'<p class="panel-note">{label}: <strong>{share} negative</strong><br>'
                        f'{negative:,} negative / {total:,} classified · Required: {minimum} classified</p>')
            if recent["classified_count"] and baseline["classified_count"]:
                change = (recent["negative_count"] / recent["classified_count"]
                          - baseline["negative_count"] / baseline["classified_count"]) * 100
                st.html(f'<p class="panel-note">Observed change: {change:+.1f} pp<br>'
                        f'Alert threshold: +{alert["min_increase_pp"]} pp with enough samples.<br>'
                        'Fixed comparison: last hour vs preceding 24 hours.</p>')
            review_button(game, snapshot)

with st.container(border=True, key="related_reviews"):
    st.html(f"<h2>{game_name} · Recent review samples</h2>")
    render_review_samples(snapshot)
