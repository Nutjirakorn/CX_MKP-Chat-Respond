"""
MKP Chat Respond Dashboard (Streamlit)

Reads the two tabs of the MKP_Chat Respond Tracker Google Sheet directly
("SUM_All Channel" and "SHP&LZD_Campaign"), so every time the sheet is
updated the dashboard picks the new data up on its next refresh.

Requirements (requirements.txt):
    streamlit>=1.32
    pandas
    plotly
    requests

The sheet must stay "Anyone with the link can view" (it already is), because
the app reads it through Google's CSV export URL, no API key needed.
"""

from __future__ import annotations

import calendar as cal
import datetime as dt
import io
import math
import re
import time

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

# ------------------------------------------------------------------ settings
SHEET_ID = "15a2tp4FV-JWUG23H5sWzogdQouO_A2g45QUph7EVMuI"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit"
TAB_DATA = "SUM_All Channel"
TAB_CAMPAIGN = "SHP&LZD_Campaign"
YEAR = 2026

CACHE_TTL_SEC = 300  # re-read the sheet at most every 5 minutes

CH = [  # key, label, colour
    ("shp", "Shopee", "#f58220"),
    ("lzd", "Lazada", "#1a73e8"),
    ("ttk", "TikTok", "#7d8590"),
]
ALL_COLOR = "#d7263d"
COLOR = {k: c for k, _, c in CH}
COLOR["all"] = ALL_COLOR
LABEL = {k: n for k, n, _ in CH}
LABEL["all"] = "All channels"

KPI = {"shp": 0.60, "lzd": 0.85, "ttk": 0.85}  # respond rate, last 30 days
KPI_WINDOW_DAYS = 30

DAY_TYPES = ["BAU", "Double Digit", "Mid Month", "Payday", "Other Campaign"]
TYPE_COLOR = {
    "BAU": "rgba(0,0,0,0)",
    "Double Digit": "rgba(215,38,61,0.14)",
    "Mid Month": "rgba(245,130,32,0.12)",
    "Payday": "rgba(26,115,232,0.10)",
    "Other Campaign": "rgba(125,133,144,0.14)",
}
PRI = {"Double Digit": 4, "Mid Month": 3, "Payday": 2, "Other Campaign": 1}


# ------------------------------------------------------------------ loading
def csv_url(tab: str) -> str:
    return (
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/gviz/tq"
        f"?tqx=out:csv&sheet={requests.utils.quote(tab)}"
    )


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner="Reading the Google Sheet…")
def fetch_tab(tab: str) -> pd.DataFrame:
    r = requests.get(csv_url(tab), timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), header=None, dtype=str, keep_default_na=False)
    return df


def _num(v) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s == "" or s.lower() in ("nan", "none", "-", "—"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(v, year: int) -> dt.date | None:
    """Accept '1-Jan', '01-Jan', '2026-01-01', '1/1/2026', 'Date(2026,0,1)'."""
    s = str(v).strip()
    if not s:
        return None
    m = re.match(r"Date\((\d{4}),(\d{1,2}),(\d{1,2})", s)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)) + 1, int(m.group(3)))
    for fmt in ("%d-%b", "%d-%B", "%d %b", "%b %d"):
        try:
            return dt.datetime.strptime(f"{s} {year}", f"{fmt} %Y").date()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%b-%Y", "%d-%b-%y"):
        try:
            return dt.datetime.strptime(s[:10] if fmt == "%Y-%m-%d" else s, fmt).date()
        except ValueError:
            pass
    return None


def parse_daily(raw: pd.DataFrame) -> pd.DataFrame:
    """SUM_All Channel: one header row with 'Date' then day columns; blocks
    'Total Chat' and 'Respond Chat' with Shopee/Lazada/TikTok rows."""
    rows = raw.values.tolist()
    year = YEAR
    for r in rows:
        for v in r[:6]:
            m = re.search(r"Year\s*(\d{4})", str(v))
            if m:
                year = int(m.group(1))
    hdr_i = next(i for i, r in enumerate(rows) if any(str(v).strip() == "Date" for v in r[:4]))
    date_col = next(j for j, v in enumerate(rows[hdr_i]) if str(v).strip() == "Date")
    dates = [_parse_date(v, year) for v in rows[hdr_i][date_col + 1:]]

    sec: dict[tuple[str, str], list] = {}
    block = None
    for r in rows[hdr_i + 1:]:
        tag = [str(v).strip() for v in r[: date_col + 1]]
        for b in ("Total Chat", "Respond Chat", "%Respond Rate"):
            if b in tag:
                block = b
        ch = next((c for c in ("Shopee", "Lazada", "TikTok") if c in tag), None)
        if block and ch:
            sec[(block, ch)] = r[date_col + 1:]

    key = {"Shopee": "shp", "Lazada": "lzd", "TikTok": "ttk"}
    out = []
    for i, d in enumerate(dates):
        if d is None:
            continue
        rec = {"date": d}
        has = False
        for ch, k in key.items():
            c = _num(sec.get(("Total Chat", ch), [None] * (i + 1))[i]) if i < len(sec.get(("Total Chat", ch), [])) else None
            r_ = _num(sec.get(("Respond Chat", ch), [None] * (i + 1))[i]) if i < len(sec.get(("Respond Chat", ch), [])) else None
            if c is not None:
                has = True
            rec[f"{k}_chat"] = int(c) if c is not None else 0
            rec[f"{k}_resp"] = int(r_) if r_ is not None else 0
        if has:
            out.append(rec)
    df = pd.DataFrame(out).sort_values("date").reset_index(drop=True)
    df["all_chat"] = df[[f"{k}_chat" for k in key.values()]].sum(axis=1)
    df["all_resp"] = df[[f"{k}_resp" for k in key.values()]].sum(axis=1)
    return df


# ---- campaign calendar -------------------------------------------------
MON = {m.upper(): i for i, m in enumerate(cal.month_abbr) if m}
MON.update({"JULY": 7, "JUNE": 6, "SEPT": 9})
NOISE = re.compile(r"เริ่มเปลี่ยน|^loop\b", re.I)
DD = re.compile(r"\b(\d{1,2})\.(\d{1,2})\b")
RANGE = re.compile(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b")
LIST = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{1,2})\b")


def _month_end(d: dt.date) -> dt.date:
    return dt.date(d.year, d.month, cal.monthrange(d.year, d.month)[1])


def _span(date: dt.date, text: str, kind: str) -> list[dt.date]:
    if kind == "dd":
        return [date + dt.timedelta(days=i) for i in range(4)]
    last = cal.monthrange(date.year, date.month)[1]
    m = LIST.search(text)
    if m:
        return [dt.date(date.year, date.month, int(x)) for x in m.groups() if int(x) <= last]
    m = RANGE.search(text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        start = dt.date(date.year, date.month, min(a, last))
        if b >= a:
            end = dt.date(date.year, date.month, min(b, last))
        else:
            nm = date.month + 1
            end = dt.date(date.year, nm, b) if nm <= 12 else _month_end(date)
        return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    if kind == "mm":
        return [dt.date(date.year, date.month, d) for d in (15, 16, 17)]
    if kind == "pd":
        s = dt.date(date.year, date.month, 25) if date.day < 25 else date
        return [s + dt.timedelta(days=i) for i in range((_month_end(date) - s).days + 1)]
    return [date]


def parse_calendar(raw: pd.DataFrame, year: int = YEAR) -> pd.DataFrame:
    """SHP&LZD_Campaign: col A month ('JAN 2026'), col B day, Lazada campaign
    names in cols D and E, Shopee campaign name in col J."""
    rows = raw.values.tolist()
    # locate the columns from the header rows
    lzd_cols, shp_col = [3, 4], 9
    for r in rows[:5]:
        cells = [str(v).strip() for v in r]
        for j, v in enumerate(cells):
            if v.startswith("Campaign Name (1)"):
                lzd_cols[0] = j
            elif v.startswith("Campaign Name (2)"):
                lzd_cols[1] = j
            elif v == "Campaign Name":
                shp_col = j
    cur_month = None
    entries = []
    for r in rows:
        a = str(r[0]) if len(r) > 0 else ""
        if a.strip():
            m = re.match(r"([A-Z]+)\s*(\d{4})", " ".join(a.upper().split()))
            cur_month = MON.get(m.group(1)[:3]) if m and int(m.group(2)) == year else None
        day = _num(r[1]) if len(r) > 1 else None
        if cur_month is None or day is None:
            continue
        try:
            date = dt.date(year, cur_month, int(day))
        except ValueError:
            continue
        for idx, src in ((lzd_cols[0], "lzd"), (lzd_cols[1], "lzd"), (shp_col, "shp")):
            if idx >= len(r):
                continue
            t = " ".join(str(r[idx]).split())
            if not t or "xxxx" in t.lower():
                continue
            entries.append((date, src, t))

    flags: dict[dt.date, dict] = {}

    def mark(d, typ, src, text, ev=None, dd_day=None):
        f = flags.setdefault(d, {"type": "BAU", "lzd": [], "shp": [], "dd_event": "", "dd_day": None})
        if PRI.get(typ, 0) > PRI.get(f["type"], 0):
            f["type"] = typ
        if ev:
            f["dd_event"], f["dd_day"] = ev, dd_day
        if text not in f[src]:
            f[src].append(text)

    for date, src, text in entries:
        if NOISE.search(text):
            continue
        low = text.lower()
        m = DD.search(text)
        if m and m.group(1) == m.group(2):
            ev = f"{int(m.group(1))}.{int(m.group(2))}"
            for i, d in enumerate(_span(date, text, "dd")):
                mark(d, "Double Digit", src, ev, ev, i + 1)
        elif "mid month" in low:
            for d in _span(date, text, "mm"):
                mark(d, "Mid Month", src, "Mid Month")
        elif "payday" in low:
            for d in _span(date, text, "pd"):
                mark(d, "Payday", src, "Payday")
        else:
            name = re.sub(r"^\s*\d{1,2}\s*-\s*\d{1,2}\s*[A-Za-z]*\s*/\s*", "", text)
            name = re.sub(r"\s*/\s*\d{1,2}\s*-\s*\d{1,2}\s*[A-Za-z]*\s*$", "", name).strip(" /")
            for d in _span(date, text, "other"):
                mark(d, "Other Campaign", src, name or text)

    # each Double Digit event is exactly 4 days from the first marked day
    first: dict[str, dt.date] = {}
    for d in sorted(flags):
        f = flags[d]
        if f["type"] == "Double Digit" and f["dd_event"]:
            first.setdefault(f["dd_event"], d)
    for ev, d0 in first.items():
        for i in range(4):
            d = d0 + dt.timedelta(days=i)
            f = flags.setdefault(d, {"type": "BAU", "lzd": [], "shp": [], "dd_event": "", "dd_day": None})
            f["type"], f["dd_event"], f["dd_day"] = "Double Digit", ev, i + 1

    out = []
    d = dt.date(year, 1, 1)
    while d.year == year:
        f = flags.get(d, {"type": "BAU", "lzd": [], "shp": [], "dd_event": "", "dd_day": None})
        out.append({
            "date": d, "weekday": d.strftime("%a"), "type": f["type"],
            "dd_event": f["dd_event"], "dd_day": f["dd_day"],
            "lzd_campaign": " / ".join(f["lzd"]), "shp_campaign": " / ".join(f["shp"]),
        })
        d += dt.timedelta(days=1)
    return pd.DataFrame(out)


@st.cache_data(ttl=CACHE_TTL_SEC, show_spinner=False)
def load_all() -> tuple[pd.DataFrame, pd.DataFrame, dt.datetime]:
    daily = parse_daily(fetch_tab(TAB_DATA))
    calendar = parse_calendar(fetch_tab(TAB_CAMPAIGN))
    daily = daily.merge(calendar, on="date", how="left")
    daily["type"] = daily["type"].fillna("BAU")
    for c in ("dd_event", "lzd_campaign", "shp_campaign"):
        daily[c] = daily[c].fillna("")
    daily["weekday"] = daily["date"].map(lambda d: d.strftime("%a"))
    return daily, calendar, dt.datetime.now()


# ------------------------------------------------------------------ helpers
def rate(resp: float, chat: float) -> float | None:
    return resp / chat if chat else None


def fmt_pct(v) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v * 100:.1f}%"


def fmt_n(v) -> str:
    return "—" if v is None else f"{int(round(v)):,}"


def bands(df: pd.DataFrame) -> list[dict]:
    """Consecutive runs of the same non-BAU day type, for shading."""
    out = []
    cur = None
    for _, r in df.iterrows():
        if r["type"] == "BAU":
            cur = None
            continue
        if cur and cur["type"] == r["type"] and cur["ev"] == r["dd_event"] and (r["date"] - cur["end"]).days == 1:
            cur["end"] = r["date"]
        else:
            cur = {"type": r["type"], "ev": r["dd_event"], "start": r["date"], "end": r["date"]}
            out.append(cur)
    return out


def add_bands(fig: go.Figure, df: pd.DataFrame) -> None:
    for b in bands(df):
        x0 = dt.datetime.combine(b["start"], dt.time()) - dt.timedelta(hours=12)
        x1 = dt.datetime.combine(b["end"], dt.time()) + dt.timedelta(hours=12)
        fig.add_vrect(x0=x0, x1=x1, fillcolor=TYPE_COLOR[b["type"]], line_width=0, layer="below",
                      annotation_text=b["ev"] if b["ev"] else None, annotation_position="top left",
                      annotation_font_size=11)


def base_layout(fig: go.Figure, height: int = 360, **kw) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.08),
                      hovermode="x unified", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", **kw)
    fig.update_yaxes(gridcolor="rgba(128,128,128,0.2)", zeroline=False)
    fig.update_xaxes(showgrid=False)
    return fig


# ------------------------------------------------------------------ page
st.set_page_config(page_title="MKP Chat Respond Dashboard", page_icon="💬", layout="wide")

try:
    daily_all, calendar_df, fetched_at = load_all()
except Exception as e:  # noqa: BLE001
    st.error(f"Could not read the Google Sheet. Check that it is still shared as 'Anyone with the link can view'. Details: {e}")
    st.stop()

if daily_all.empty:
    st.error("No chat data found in the 'SUM_All Channel' tab.")
    st.stop()

dmin, dmax = daily_all["date"].min(), daily_all["date"].max()

# ---- sidebar: filters ---------------------------------------------------
with st.sidebar:
    st.title("MKP Chat Respond")
    st.caption(f"Source: [Google Sheet]({SHEET_URL})  \nData through **{dmax:%d %b %Y}**  \nLast read {fetched_at:%d %b %H:%M}")
    if st.button("🔄 Refresh now", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    auto = st.toggle("Auto refresh", value=True, help="Re-reads the sheet on a timer while this page is open.")
    interval = st.select_slider("Every", options=[1, 2, 5, 10, 15, 30], value=5, format_func=lambda m: f"{m} min", disabled=not auto)

    st.divider()
    st.subheader("Time frame")
    if "range" not in st.session_state:
        st.session_state["range"] = (max(dmin, dmax - dt.timedelta(days=89)), dmax)

    def preset(days: int | None):
        start = dt.date(dmax.year, 1, 1) if days is None else dmax - dt.timedelta(days=days - 1)
        st.session_state["range"] = (max(dmin, start), dmax)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.button("L7D", on_click=preset, args=(7,), use_container_width=True)
    c2.button("L30D", on_click=preset, args=(30,), use_container_width=True)
    c3.button("L60D", on_click=preset, args=(60,), use_container_width=True)
    c4.button("L90D", on_click=preset, args=(90,), use_container_width=True)
    c5.button("YTD", on_click=preset, args=(None,), use_container_width=True)
    rng = st.date_input("Custom period", value=st.session_state["range"], min_value=dmin, max_value=dmax, key="range_input")
    if isinstance(rng, tuple) and len(rng) == 2:
        st.session_state["range"] = rng
    start, end = st.session_state["range"]
    day_type = st.selectbox("Day type", ["All days"] + DAY_TYPES)

    st.divider()
    st.subheader("Resource planning")
    chats_per_agent = st.number_input("Chats one agent can handle per day", min_value=10, max_value=1000, value=150, step=10)
    target_rate = st.slider("Target respond rate for planning", 0.5, 1.0, 0.85, 0.05)

mask = (daily_all["date"] >= start) & (daily_all["date"] <= end)
in_range = daily_all[mask].copy()
rows = in_range if day_type == "All days" else in_range[in_range["type"] == day_type]

# ---- header -------------------------------------------------------------
st.title("MKP Chat Respond Dashboard")
st.caption(f"Showing **{start:%d %b %Y} to {end:%d %b %Y}** ({len(rows)} days"
           + (f", {day_type} only" if day_type != "All days" else "") + "). Colours: all channels red, Shopee orange, Lazada blue, TikTok grey.")

# ---- KPI monitor (fixed last-30-day windows) ----------------------------
st.subheader("KPI monitor: respond rate, last 30 days of data")
win = daily_all.tail(KPI_WINDOW_DAYS)
kpi_rows = []
for k, name, _ in CH:
    chat, resp = int(win[f"{k}_chat"].sum()), int(win[f"{k}_resp"].sum())
    r = rate(resp, chat)
    kpi_rows.append({"key": k, "name": name, "chat": chat, "resp": resp, "rate": r, "kpi": KPI[k],
                     "ok": r is not None and r >= KPI[k], "gap_chats": max(0, math.ceil(KPI[k] * chat) - resp)})
cols = st.columns(3)
for col, kr in zip(cols, kpi_rows):
    with col:
        delta = None if kr["rate"] is None else f"{(kr['rate'] - kr['kpi']) * 100:+.1f} pts vs KPI {kr['kpi'] * 100:.0f}%"
        st.metric(f"{kr['name']} respond rate", fmt_pct(kr["rate"]), delta, delta_color="normal")
        st.caption(f"{win['date'].min():%d %b} to {win['date'].max():%d %b} · {fmt_n(kr['resp'])} of {fmt_n(kr['chat'])} chats"
                   + ("" if kr["ok"] else f" · needs **{fmt_n(kr['gap_chats'])}** more responded chats"))
below = [kr for kr in kpi_rows if not kr["ok"]]
if below:
    st.error("Below KPI: " + ", ".join(f"{kr['name']} {fmt_pct(kr['rate'])} (KPI {kr['kpi'] * 100:.0f}%)" for kr in below))
else:
    st.success("All three channels meet their respond-rate KPI on the last 30 days.")

# ---- period summary tiles -----------------------------------------------
st.subheader("Chat volume and respond rate in the selected period")
tiles = st.columns(4)
for col, k in zip(tiles, ["all", "shp", "lzd", "ttk"]):
    chat, resp = int(rows[f"{k}_chat"].sum()), int(rows[f"{k}_resp"].sum())
    r = rate(resp, chat)
    n = max(len(rows), 1)
    with col:
        st.markdown(f"<div style='border-left:6px solid {COLOR[k]};padding-left:10px'><b>{LABEL[k]}</b></div>", unsafe_allow_html=True)
        a, b = st.columns(2)
        a.metric("Total chats", fmt_n(chat), f"{chat / n:,.0f} per day", delta_color="off")
        b.metric("Responded", fmt_n(resp), f"rate {fmt_pct(r)}", delta_color="off")
        if k != "all" and r is not None:
            st.caption(("✅ above" if r >= KPI[k] else "⚠️ below") + f" KPI {KPI[k] * 100:.0f}% in this period")

# ---- daily volume -------------------------------------------------------
st.subheader("Daily chat volume by channel (solid = responded, pale = not responded)")
fig = go.Figure()
x = pd.to_datetime(in_range["date"])
for k, name, c in CH:
    fig.add_bar(x=x, y=in_range[f"{k}_resp"], name=f"{name} responded", marker_color=c, offsetgroup="a",
                hovertemplate="%{y:,}")
    fig.add_bar(x=x, y=in_range[f"{k}_chat"] - in_range[f"{k}_resp"], name=f"{name} not responded",
                marker_color=c, marker_opacity=0.3, offsetgroup="a", hovertemplate="%{y:,}")
fig.add_scatter(x=x, y=in_range["all_chat"], name="All channels total", mode="lines", line=dict(color=ALL_COLOR, width=1.5),
                hovertemplate="%{y:,}")
fig.update_layout(barmode="stack")
add_bands(fig, in_range)
st.plotly_chart(base_layout(fig, 400), use_container_width=True)
st.caption("Shaded bands are campaign days from the SHP&LZD campaign plan (red = Double Digit, orange = Mid Month, blue = Payday, grey = other campaigns).")

# ---- daily respond rate -------------------------------------------------
st.subheader("Daily respond rate vs KPI")
roll = st.checkbox("7-day rolling rate", value=len(in_range) > 45)
fig = go.Figure()
for k, name, c in CH:
    if roll:
        r = in_range[f"{k}_resp"].rolling(7, min_periods=1).sum() / in_range[f"{k}_chat"].rolling(7, min_periods=1).sum()
    else:
        r = in_range[f"{k}_resp"] / in_range[f"{k}_chat"].replace(0, pd.NA)
    fig.add_scatter(x=x, y=r, name=name, mode="lines", line=dict(color=c, width=2), hovertemplate="%{y:.1%}")
fig.add_hline(y=KPI["shp"], line_dash="dash", line_color=COLOR["shp"], annotation_text="Shopee KPI 60%", annotation_position="right")
fig.add_hline(y=KPI["lzd"], line_dash="dash", line_color=COLOR["lzd"], annotation_text="Lazada / TikTok KPI 85%", annotation_position="right")
fig.update_yaxes(tickformat=".0%", range=[0, 1.05])
add_bands(fig, in_range)
st.plotly_chart(base_layout(fig, 320), use_container_width=True)

# ---- campaign impact ----------------------------------------------------
st.subheader("Campaign impact: baseline vs campaign days")
stats = []
for t in DAY_TYPES:
    sub = in_range[in_range["type"] == t]
    if sub.empty:
        continue
    rec = {"Day type": t, "Days": len(sub)}
    for k in ["all", "shp", "lzd", "ttk"]:
        rec[f"{LABEL[k]} chats/day"] = sub[f"{k}_chat"].mean()
        rec[f"{LABEL[k]} rate"] = rate(sub[f"{k}_resp"].sum(), sub[f"{k}_chat"].sum())
    stats.append(rec)
stats_df = pd.DataFrame(stats)
bau_avg = float(stats_df.loc[stats_df["Day type"] == "BAU", "All channels chats/day"].iloc[0]) if "BAU" in set(stats_df["Day type"]) else None
c1, c2 = st.columns([1, 1.4])
with c1:
    fig = go.Figure()
    for k, name, c in CH:
        fig.add_bar(x=stats_df["Day type"], y=stats_df[f"{name} chats/day"], name=name, marker_color=c, hovertemplate="%{y:,.0f}")
    fig.update_layout(barmode="stack")
    if bau_avg:
        fig.add_hline(y=bau_avg, line_dash="dash", line_color=ALL_COLOR, annotation_text=f"BAU {bau_avg:,.0f}/day")
    st.plotly_chart(base_layout(fig, 320, title="Average chats per day by day type"), use_container_width=True)
with c2:
    show = stats_df.copy()
    if bau_avg:
        show["x BAU"] = show["All channels chats/day"] / bau_avg
    for col in show.columns:
        if col.endswith("rate"):
            show[col] = show[col].map(fmt_pct)
        elif col.endswith("chats/day"):
            show[col] = show[col].map(lambda v: f"{v:,.0f}")
    if "x BAU" in show:
        show["x BAU"] = show["x BAU"].map(lambda v: f"{v:.2f}×")
    st.dataframe(show, hide_index=True, use_container_width=True)

# ---- Double Digit trend -------------------------------------------------
st.subheader("Double Digit trend")
dd_src = in_range if day_type in ("All days", "Double Digit") else in_range.iloc[0:0]
events = []
for ev, g in dd_src[dd_src["type"] == "Double Digit"].groupby("dd_event", sort=False):
    g = g.sort_values("date")
    peak = g.loc[g["all_chat"].idxmax()]
    rec = {"Event": ev, "From": g["date"].min(), "To": g["date"].max(), "Days": len(g),
           "All chats": int(g["all_chat"].sum()), "Responded": int(g["all_resp"].sum()),
           "All rate": rate(g["all_resp"].sum(), g["all_chat"].sum()),
           "Peak day": f"{peak['date']:%a %d %b} (day {int(peak['dd_day']) if pd.notna(peak['dd_day']) else '?'}) · {int(peak['all_chat']):,}"}
    for k, name, _ in CH:
        rec[name] = int(g[f"{k}_chat"].sum())
        rec[f"{name} resp"] = int(g[f"{k}_resp"].sum())
        rec[f"{name} rate"] = rate(g[f"{k}_resp"].sum(), g[f"{k}_chat"].sum())
    rec["_days"] = g
    events.append(rec)
events.sort(key=lambda e: e["From"])
if not events:
    st.info("No Double Digit event in the selected period. Widen the time frame to see the trend.")
else:
    c1, c2 = st.columns(2)
    with c1:
        fig = go.Figure()
        xs = [e["Event"] for e in events]
        for k, name, c in CH:
            fig.add_bar(x=xs, y=[e[f"{name} resp"] for e in events], name=f"{name} responded", marker_color=c, hovertemplate="%{y:,}")
            fig.add_bar(x=xs, y=[e[name] - e[f"{name} resp"] for e in events], name=f"{name} not responded", marker_color=c, marker_opacity=0.3, hovertemplate="%{y:,}")
        fig.update_layout(barmode="stack")
        if bau_avg:
            fig.add_hline(y=bau_avg * 4, line_dash="dash", line_color=ALL_COLOR, annotation_text="4 BAU days")
        st.plotly_chart(base_layout(fig, 320, title="Chat volume per Double Digit event (4 days)"), use_container_width=True)
    with c2:
        # day-of-campaign profile: average chats on day 1..4 and the day before, as a multiple of BAU
        bau_rows = in_range[in_range["type"] == "BAU"]
        prof = []
        for dnum in [0, 1, 2, 3, 4]:
            vals = {k: [] for k, _, _ in CH}
            for e in events:
                if dnum == 0:
                    i = daily_all.index[daily_all["date"] == e["From"]]
                    r = daily_all.iloc[i[0] - 1] if len(i) and i[0] > 0 else None
                else:
                    g = e["_days"]
                    hit = g[g["dd_day"] == dnum]
                    r = hit.iloc[0] if len(hit) else None
                if r is not None:
                    for k, _, _ in CH:
                        vals[k].append(r[f"{k}_chat"])
            if vals["shp"]:
                lab = {0: "Day before", 1: "Day 1 (launch 20:00)", 2: "Day 2 (DD date)", 3: "Day 3", 4: "Day 4"}[dnum]
                rec = {"Day": lab}
                for k, name, _ in CH:
                    rec[name] = sum(vals[k]) / len(vals[k])
                prof.append(rec)
        prof_df = pd.DataFrame(prof)
        fig = go.Figure()
        for k, name, c in CH:
            fig.add_bar(x=prof_df["Day"], y=prof_df[name], name=name, marker_color=c, hovertemplate="%{y:,.0f}")
        fig.update_layout(barmode="stack")
        if bau_avg:
            fig.add_hline(y=bau_avg, line_dash="dash", line_color=ALL_COLOR, annotation_text="BAU day")
        st.plotly_chart(base_layout(fig, 320, title="Day-of-campaign profile (average chats)"), use_container_width=True)
    ev_tbl = pd.DataFrame([{k: v for k, v in e.items() if not k.startswith("_")} for e in events])
    for col in ev_tbl.columns:
        if col.endswith("rate"):
            ev_tbl[col] = ev_tbl[col].map(fmt_pct)
    ev_tbl["From"] = ev_tbl["From"].map(lambda d: f"{d:%d %b}")
    ev_tbl["To"] = ev_tbl["To"].map(lambda d: f"{d:%d %b}")
    st.dataframe(ev_tbl, hide_index=True, use_container_width=True)

# ---- weekday pattern ----------------------------------------------------
st.subheader("Weekday pattern (average chats per day, selected period)")
order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
dow = rows.groupby("weekday")[[f"{k}_chat" for k, _, _ in CH]].mean().reindex(order)
fig = go.Figure()
for k, name, c in CH:
    fig.add_bar(x=order, y=dow[f"{k}_chat"], name=name, marker_color=c, hovertemplate="%{y:,.0f}")
fig.update_layout(barmode="stack")
st.plotly_chart(base_layout(fig, 280), use_container_width=True)

# ---- resource plan ------------------------------------------------------
st.subheader("Chat agent resource plan for the next Double Digit")
upcoming = calendar_df[(calendar_df["type"] == "Double Digit") & (calendar_df["date"] > dmax)]
hist_dd = daily_all[daily_all["type"] == "Double Digit"]
recent_bau = daily_all[daily_all["type"] == "BAU"].tail(30)
if upcoming.empty:
    st.info("No upcoming Double Digit event in the campaign sheet yet. Add it to SHP&LZD_Campaign and the plan appears here.")
elif hist_dd.empty or recent_bau.empty:
    st.info("Not enough history to estimate the next event.")
else:
    ev_choice = st.selectbox("Event", list(dict.fromkeys(upcoming["dd_event"])))
    ev_days = upcoming[upcoming["dd_event"] == ev_choice].sort_values("date")
    # multiplier per campaign day = avg chats on that day number over the last 3 events / avg BAU day at the time
    last_events = list(dict.fromkeys(hist_dd.sort_values("date")["dd_event"]))[-3:]
    plan = []
    for _, d in ev_days.iterrows():
        rec = {"Date": f"{d['date']:%a %d %b}", "Campaign day": int(d["dd_day"])}
        total_est = 0
        for k, name, _ in CH:
            mults = []
            for ev in last_events:
                g = hist_dd[hist_dd["dd_event"] == ev]
                hit = g[g["dd_day"] == d["dd_day"]]
                if len(hit):
                    bau_then = daily_all[(daily_all["type"] == "BAU") & (daily_all["date"] < g["date"].min())].tail(30)[f"{k}_chat"].mean()
                    if bau_then:
                        mults.append(hit.iloc[0][f"{k}_chat"] / bau_then)
            mult = sum(mults) / len(mults) if mults else 1.0
            est = recent_bau[f"{k}_chat"].mean() * mult
            total_est += est
            rec[f"{name} est."] = est
        rec["All est. chats"] = total_est
        rec["Agents needed"] = math.ceil(total_est * target_rate / chats_per_agent)
        plan.append(rec)
    plan_df = pd.DataFrame(plan)
    peak_i = plan_df["All est. chats"].idxmax()
    st.markdown(
        f"Estimate for **{ev_choice}** ({ev_days['date'].min():%d %b} to {ev_days['date'].max():%d %b}), based on the last {len(last_events)} Double Digit events "
        f"({', '.join(last_events)}) scaled to the current BAU baseline of **{recent_bau['all_chat'].mean():,.0f} chats/day**. "
        f"Expected spike on **{plan_df.loc[peak_i, 'Date']}** with about **{plan_df.loc[peak_i, 'All est. chats']:,.0f} chats**; "
        f"standby **{int(plan_df.loc[peak_i, 'Agents needed'])} agents** that day at {chats_per_agent} chats/agent and a {target_rate:.0%} target."
    )
    show = plan_df.copy()
    for col in show.columns:
        if col.endswith("est.") or col == "All est. chats":
            show[col] = show[col].map(lambda v: f"{v:,.0f}")
    st.dataframe(show, hide_index=True, use_container_width=True)

# ---- upcoming campaigns -------------------------------------------------
with st.expander("Upcoming campaign calendar (next 60 days)"):
    up = calendar_df[(calendar_df["date"] > dmax) & (calendar_df["date"] <= dmax + dt.timedelta(days=60)) & (calendar_df["type"] != "BAU")]
    st.dataframe(up[["date", "weekday", "type", "dd_event", "dd_day", "lzd_campaign", "shp_campaign"]], hide_index=True, use_container_width=True)

# ---- suggestions --------------------------------------------------------
st.subheader("Suggestions")
sugs = []
for kr in kpi_rows:
    k = kr["key"]
    if not kr["ok"] and kr["rate"] is not None:
        wdf = win[win[f"{k}_chat"] > 0]
        low_days = int((wdf[f"{k}_resp"] / wdf[f"{k}_chat"] < KPI[k]).sum())
        missed = kr["chat"] - kr["resp"]
        sugs.append(
            f"**{kr['name']} is {(KPI[k] - kr['rate']) * 100:.1f} points below its {KPI[k]:.0%} KPI** on the last 30 days. "
            f"{missed:,} chats went unanswered ({missed / KPI_WINDOW_DAYS:,.0f} per day) and {low_days} of {len(wdf)} days were below KPI. "
            f"To reach the KPI on the same volume the team needs about {math.ceil(kr['gap_chats'] / KPI_WINDOW_DAYS):,} more responded chats per day."
        )
        worst = wdf.assign(r=wdf[f"{k}_resp"] / wdf[f"{k}_chat"]).nsmallest(3, "r")
        sugs.append("Worst days: " + "; ".join(f"{r['date']:%a %d %b} {r['r']:.0%} ({r['type']})" for _, r in worst.iterrows())
                    + ". Check whether those were campaign days or staffing gaps (weekend / holiday).")
        if k == "ttk":
            sugs.append("TikTok volume has grown far beyond its January level; the gap is structural. Consider a dedicated TikTok agent, auto-reply for the top FAQ, and a 24-hour catch-up rule.")
if not upcoming.empty and not hist_dd.empty:
    sugs.append(f"Next Double Digit **{list(dict.fromkeys(upcoming['dd_event']))[0]}** starts {upcoming['date'].min():%d %b}: use the resource plan above and brief agents 2 days before launch (volume historically rises from the launch evening, day 1 at 20:00).")
for t in ("Double Digit", "Mid Month", "Payday", "Other Campaign"):
    sub = in_range[in_range["type"] == t]
    bau = in_range[in_range["type"] == "BAU"]
    if len(sub) and len(bau):
        r_c, r_b = rate(sub["all_resp"].sum(), sub["all_chat"].sum()), rate(bau["all_resp"].sum(), bau["all_chat"].sum())
        if r_c is not None and r_b is not None and r_c < r_b - 0.05:
            sugs.append(f"{t} days respond at {r_c:.0%} vs {r_b:.0%} on BAU days in this period: campaign volume is outrunning capacity, so add standby agents on {t} days.")
if not sugs:
    sugs.append("All channels are on KPI and campaign days hold their respond rate. Keep the current staffing and re-check before the next Double Digit.")
for s in sugs:
    st.markdown(f"- {s}")

# ---- data ---------------------------------------------------------------
with st.expander("Daily data (selected period)"):
    show = rows[["date", "weekday", "type", "dd_event", "dd_day", "lzd_campaign", "shp_campaign",
                 "all_chat", "all_resp", "shp_chat", "shp_resp", "lzd_chat", "lzd_resp", "ttk_chat", "ttk_resp"]].copy()
    for k in ["all", "shp", "lzd", "ttk"]:
        show[f"{k}_rate"] = (show[f"{k}_resp"] / show[f"{k}_chat"].replace(0, pd.NA)).map(fmt_pct)
    st.dataframe(show, hide_index=True, use_container_width=True)
    st.download_button("Download CSV", show.to_csv(index=False).encode("utf-8"), "mkp_chat_daily.csv", "text/csv")

# ---- auto refresh -------------------------------------------------------
if auto:
    time.sleep(interval * 60)
    st.cache_data.clear()
    st.rerun()