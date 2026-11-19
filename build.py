#!/usr/bin/env python3
"""
Rebuilds the tracker's data files from the public NBA injury-report archive
(sportsdataverse "nba_stats_injury_report" release, which republishes the NBA's official reports).

Writes into docs/:  stays.csv, meta.json  (and current.csv only if it does not exist yet; fetch_now.py keeps that one fresh)
Reads  data/stays_past.csv (finished seasons, built once) and downloads only the current season's file.
"""
import io, json, os, re, sys, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

BASE = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/nba_stats_injury_report/injury_report_%d.csv"
SHORT = {"Portland Trail Blazers": "Blazers", "Philadelphia 76ers": "76ers", "Los Angeles Lakers": "Lakers", "LA Clippers": "Clippers"}
short = lambda t: SHORT.get(t, str(t).split()[-1])
now_et = datetime.now(ZoneInfo("America/New_York"))
today = now_et.date()
cutoff = str(today - timedelta(days=1))           # games up to yesterday are finished
end_year = today.year + 1 if today.month >= 8 else today.year
season_name = lambda y: f"{y-1}-{str(y)[2:]}"
CURRENT = end_year

def fetch(year):
    print("downloading", year, flush=True)
    req = urllib.request.Request(BASE % year, headers={"User-Agent": "injury-tracker"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return pd.read_csv(io.BytesIO(r.read()), low_memory=False)

def flip(n):
    if not isinstance(n, str): return None
    if ", " in n:
        a, b = n.split(", ", 1); return f"{b} {a}"
    return n

def cat(r):
    if r.startswith("Injury/Illness"):
        return "Illness" if r.endswith("- Illness") or "N/A; Illness" in r else "Injury"
    if r.startswith("Rest - "): return "Rest (injury management)"
    if r.startswith("Return to Competition"): return "Reconditioning"
    return None

def prep(d, year):
    d = d.copy()
    d["Season"] = season_name(year)
    d["gid"] = d.game_id.map(lambda v: f"{int(v):010d}" if pd.notna(v) else None)
    d["Phase"] = d.gid.str[:3].map({"002": "Regular season", "004": "Playoffs", "005": "Play-in"})
    d = d[d.Phase.notna()].copy()
    d["Team"] = d.team.map(short)
    d["rd"] = pd.to_datetime(d.report_datetime)
    return d

def latest_versions(d):
    p = d[d.report_player_name.notna()].sort_values("rd")
    p = p.drop_duplicates(["gid", "Team", "report_player_name"], keep="last").copy()
    reason = p.reason.fillna("")
    p["Category"] = reason.map(cat)
    p["Injury"] = (reason.str.replace(r"^(Injury/Illness|Rest|Return to Competition Reconditioning)\s*-?\s*", "", regex=True)
                   .str.replace(r"^N/A;\s*", "", regex=True).str.strip())
    p.loc[p.Category == "Reconditioning", "Injury"] = "Return to competition reconditioning"
    p["Player"] = p.report_player_name.map(flip)
    return p

def build_stays(d, only_finished=True):
    d = d[d.game_date <= cutoff] if only_finished else d
    games = d[["Season", "Team", "game_date", "gid"]].drop_duplicates(["Season", "Team", "gid"]).sort_values(["Season", "Team", "game_date", "gid"])
    games["idx"] = games.groupby(["Season", "Team"]).cumcount()
    p = latest_versions(d)
    out = p[(p.current_status == "Out") & p.Category.notna()].merge(games[["Season", "Team", "gid", "idx"]], on=["Season", "Team", "gid"], how="left")
    out = out.sort_values(["Season", "Team", "Player", "idx"])
    last = games.groupby(["Season", "Team"]).idx.max()
    rows = []
    for (s, t, pl), g in out.groupby(["Season", "Team", "Player"], sort=False):
        g = g.drop_duplicates("idx"); idx = g.idx.values
        run = np.r_[0, np.cumsum(np.diff(idx) > 1)]
        for k in np.unique(run):
            h = g[run == k]
            rows.append(dict(Season=s, Team=t, Player=pl, Category=h.Category.mode().iat[0], Injury=h.Injury.mode().iat[0],
                             Start_Date=h.game_date.min(), End_Date=h.game_date.max(), Games_Out=len(h), Phase=h.Phase.mode().iat[0],
                             Status="Ongoing" if h.idx.max() == last[(s, t)] else "Closed"))
    S = pd.DataFrame(rows)
    S["Days_Span"] = (pd.to_datetime(S.End_Date) - pd.to_datetime(S.Start_Date)).dt.days + 1
    return S[["Season", "Team", "Player", "Category", "Injury", "Start_Date", "End_Date", "Games_Out", "Days_Span", "Phase", "Status"]]

def build_current(d):
    """Latest filed report per team: the team's most recent game (today or later) that has real rows."""
    d = d[d.game_date >= str(today)]
    p = latest_versions(d)
    filed = p[p.current_status.notna()]
    teams_all = sorted(set(d.Team))
    rows, missing = [], []
    for t in teams_all:
        ft = filed[filed.Team == t]
        if ft.empty: missing.append(t); continue
        gd = ft.game_date.min()                       # the team's next game with a filed report
        g = ft[ft.game_date == gd]
        for r in g.itertuples():
            c = cat(r.reason if isinstance(r.reason, str) else "")
            if c is None or r.current_status == "Out" and False: continue
            rows.append(dict(Team=t, Player=flip(r.report_player_name), Status=r.current_status, Category=c,
                             Injury=re.sub(r"^(Injury/Illness|Rest|Return to Competition Reconditioning)\s*-?\s*", "", r.reason).replace("N/A; ", "").strip() or "Return to competition reconditioning",
                             Game_Date=r.game_date, Matchup=r.report_matchup, Report_Time=r.report_datetime[:16].replace("T", " ")))
    C = pd.DataFrame(rows, columns=["Team", "Player", "Status", "Category", "Injury", "Game_Date", "Matchup", "Report_Time"])
    return C, missing

PB = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_player_boxscores/player_box_%d.csv"
ESPN_AB = {"GS": "GSW", "NY": "NYK", "SA": "SAS", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"}
def norm(n):
    import unicodedata
    n = unicodedata.normalize("NFKD", str(n)).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"[.\']", "", n); n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n)
    return re.sub(r"\s+", " ", n).strip()

def build_positions(S):
    """Small and power forwards (and generic 'F') by team: ESPN position from the box scores, plus players
    who have a 2026-27 stay but have not played this season (position taken from last season)."""
    def load(y):
        try:
            d = pd.read_csv(io.BytesIO(urllib.request.urlopen(urllib.request.Request(PB % y, headers={"User-Agent": "injury-tracker"}), timeout=300).read()), low_memory=False)
        except Exception as e:
            print("positions: could not load", y, e); return pd.DataFrame()
        d = d[d.athlete_position_abbreviation.notna()].copy()
        d["ab"] = d.team_abbreviation.map(lambda a: ESPN_AB.get(a, a)); d["min"] = pd.to_numeric(d.minutes, errors="coerce")
        d["did"] = d["min"].fillna(0) > 0
        return d
    cur, prev = load(CURRENT), load(CURRENT - 1)
    ab2short = {"ATL":"Hawks","BOS":"Celtics","BKN":"Nets","CHA":"Hornets","CHI":"Bulls","CLE":"Cavaliers","DAL":"Mavericks","DEN":"Nuggets","DET":"Pistons","GSW":"Warriors","HOU":"Rockets","IND":"Pacers","LAC":"Clippers","LAL":"Lakers","MEM":"Grizzlies","MIA":"Heat","MIL":"Bucks","MIN":"Timberwolves","NOP":"Pelicans","NYK":"Knicks","OKC":"Thunder","ORL":"Magic","PHI":"76ers","PHX":"Suns","POR":"Blazers","SAC":"Kings","SAS":"Spurs","TOR":"Raptors","UTA":"Jazz","WAS":"Wizards"}
    short2ab = {v: k for k, v in ab2short.items()}
    out, seen = {}, set()
    stay_names = {norm(n): n for n in S.Player.unique()}
    def add(ab, name, pos, gp, mpg, source, starts=0, l5=0, l5n=0):
        key = (ab, norm(name))
        if key in seen or pos not in ("SF", "PF", "F", "G"): return
        seen.add(key)
        out.setdefault(ab, []).append(dict(name=name, stay_name=stay_names.get(norm(name), name), pos=pos, gp=int(gp), mpg=round(float(mpg), 1) if mpg == mpg else None, src=source, starts=int(starts), l5=int(l5), l5n=int(l5n)))
    if len(cur):
        c = cur.sort_values(["game_date", "game_id"])
        last5 = {ab: set(gd.drop_duplicates("game_id").tail(5).game_id) for ab, gd in c.groupby("ab")}
        for aid, g in c.groupby("athlete_id"):
            last = g.iloc[-1]; played = g[g.did]
            st = played.starter.astype(bool); l5 = last5.get(last.ab, set())
            add(last.ab, last.athlete_display_name, last.athlete_position_abbreviation, len(played), played["min"].mean() if len(played) else float("nan"), "season",
                st.sum(), played[played.game_id.isin(l5)].starter.astype(bool).sum(), len(l5))
    covered = {norm(r["name"]) for v in out.values() for r in v}
    if len(prev):
        pv = prev.sort_values("game_date").groupby("athlete_id").tail(1).set_index(prev.sort_values("game_date").groupby("athlete_id").tail(1).athlete_display_name.map(norm))
        cs = S[S.Season == season_name(CURRENT)]
        for (team, pl), _ in cs.groupby(["Team", "Player"]):
            n = norm(pl)
            if n in covered or n not in pv.index or team not in short2ab: continue
            r = pv.loc[n]; r = r.iloc[-1] if hasattr(r, "iloc") and getattr(r, "ndim", 1) > 1 else r
            add(short2ab[team], pl, r.athlete_position_abbreviation, 0, float("nan"), "last season")
    for v in out.values(): v.sort(key=lambda r: -(r["mpg"] or 0))
    return out

def main():
    past = pd.read_csv("data/stays_past.csv")
    cur_name, prev_name = season_name(CURRENT), season_name(CURRENT - 1)
    d_cur = prep(fetch(CURRENT), CURRENT)
    frames = [past[~past.Season.isin([cur_name])]]
    if prev_name not in set(past.Season):                 # the season just ended and is not saved yet
        frames.append(build_stays(prep(fetch(CURRENT - 1), CURRENT - 1), only_finished=False))
    frames.append(build_stays(d_cur))
    S = pd.concat(frames, ignore_index=True).sort_values(["Season", "Team", "Start_Date", "Player"])
    if prev_name not in set(past.Season):
        S[S.Season != cur_name].to_csv("data/stays_past.csv", index=False)
    C, missing = build_current(d_cur)
    S.to_csv("docs/stays.csv", index=False)
    if not os.path.exists("docs/current.csv"):           # fetch_now.py owns this file; only seed it the very first time
        C.to_csv("docs/current.csv", index=False)
    meta = dict(built_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), season=cur_name,
                games_through=cutoff, latest_report=str(d_cur.report_datetime.max())[:16].replace("T", " "),
                teams_not_filed=missing, stays=len(S))
    try:
        pos = build_positions(S)
        if pos: json.dump(dict(built_at_utc=meta["built_at_utc"], teams=pos), open("docs/positions.json", "w"))
        print("positions:", sum(len(v) for v in pos.values()), "forwards")
    except Exception as e:
        print("positions skipped:", repr(e))
    json.dump(meta, open("docs/meta.json", "w"))
    print(meta)

if __name__ == "__main__":
    main()
