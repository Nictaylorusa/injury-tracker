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
    json.dump(meta, open("docs/meta.json", "w"))
    print(meta)

if __name__ == "__main__":
    main()
