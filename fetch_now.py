#!/usr/bin/env python3
"""
Refreshes the "Right now" tab from the NBA's own injury-report PDFs (published about every 15 minutes).

Writes into docs/:  current.csv, now.json
It never touches stays.csv (that is build.py's job, once a day).

If the NBA server can't be reached, or a report can't be read, it leaves current.csv alone and records
the problem in now.json so the page can say so. It never crashes the schedule.
"""
import csv, io, json, os, re, sys, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PDF = "https://ak-static.cms.nba.com/referee/injury/Injury-Report_%s.pdf"
HEAD = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "application/pdf,text/html,*/*"}
CUR_COLS = ["Team", "Player", "Status", "Category", "Injury", "Game_Date", "Matchup", "Report_Time", "Game_Time"]
STATUSES = ("Out", "Doubtful", "Questionable", "Probable", "Available")
NICK = {"Hawks": "Hawks", "Celtics": "Celtics", "Nets": "Nets", "Hornets": "Hornets", "Bulls": "Bulls", "Cavaliers": "Cavaliers",
        "Mavericks": "Mavericks", "Nuggets": "Nuggets", "Pistons": "Pistons", "Warriors": "Warriors", "Rockets": "Rockets",
        "Pacers": "Pacers", "Clippers": "Clippers", "Lakers": "Lakers", "Grizzlies": "Grizzlies", "Heat": "Heat", "Bucks": "Bucks",
        "Timberwolves": "Timberwolves", "Pelicans": "Pelicans", "Knicks": "Knicks", "Thunder": "Thunder", "Magic": "Magic",
        "76ers": "76ers", "Suns": "Suns", "TrailBlazers": "Blazers", "Blazers": "Blazers", "Kings": "Kings", "Spurs": "Spurs",
        "Raptors": "Raptors", "Jazz": "Jazz", "Wizards": "Wizards"}
NICK_KEYS = sorted(NICK, key=len, reverse=True)       # longest first so "Hornets" wins over "Nets"


def get(url, tries=2, timeout=30):
    """Returns (status, bytes). status is an int HTTP code, or 0 for a network failure."""
    last = (0, b"")
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEAD), timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            last = (e.code, b"")
            if e.code in (403, 404):
                return last
        except Exception as e:                           # timeouts, DNS, proxy refusals
            last = (0, str(e).encode()[:200])
        time.sleep(2)
    return last


def stamp(dt):
    return dt.strftime("%Y-%m-%d_%I_%M%p")               # 2026-11-18_09_00PM


def parse_stamp(s):
    return datetime.strptime(s, "%Y-%m-%d_%I_%M%p").replace(tzinfo=ET)


def find_latest(now_et):
    """Walks back in 15-minute steps from now until a report file exists. Returns (stamp, pdf bytes, problem)."""
    t = now_et.replace(minute=now_et.minute - now_et.minute % 15, second=0, microsecond=0)
    blocked = 0
    for i in range(0, 12 * 4 + 1):                       # up to 12 hours back
        s = stamp(t - timedelta(minutes=15 * i))
        code, body = get(PDF % s, tries=1 if i else 2)
        if code == 200 and body[:4] == b"%PDF":
            return s, body, None
        if code != 404:
            blocked += 1
            if blocked >= 3:
                return None, None, f"The NBA server did not answer normally (last reply: {code or 'no connection'})."
    return None, None, "No report file found in the last 12 hours."


def pdf_lines(body):
    import pdfplumber
    lines = []
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(x_tolerance=2, y_tolerance=3, keep_blank_chars=False)
            rows = []
            for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
                if rows and abs(rows[-1][0] - w["top"]) <= 3:
                    rows[-1][1].append(w)
                else:
                    rows.append([w["top"], [w]])
            for _, ws in rows:
                lines.append([w["text"] for w in sorted(ws, key=lambda w: w["x0"])])
    return lines


def dump_layout(body, path="data/last_report_words.tsv"):
    """TEMPORARY debugging aid: saves where every word sits on the page, so the reader can be matched to the real layout."""
    import pdfplumber
    os.makedirs("data", exist_ok=True)
    out = []
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        for n, page in enumerate(pdf.pages[:3], 1):
            out.append(f"#page {n} size {page.width:.0f}x{page.height:.0f} lines {len(page.lines)} rects {len(page.rects)}")
            out += [f"#hline y={l['top']:.1f} x0={l['x0']:.0f} x1={l['x1']:.0f}" for l in page.lines[:60]]
            out += [f"#rect y={r['top']:.1f} h={r['height']:.1f} x0={r['x0']:.0f} x1={r['x1']:.0f}" for r in page.rects[:60]]
            for w in sorted(page.extract_words(x_tolerance=2, y_tolerance=3), key=lambda w: (round(w["top"], 1), w["x0"]))[:700]:
                out.append(f"{w['top']:.1f}\t{w['x0']:.1f}\t{w['x1']:.1f}\t{w['text']}")
    open(path, "w").write("\n".join(out))


def team_of(tokens):
    """If the tokens start with a team name (before any 'Last, First' player), returns (short name, tokens used)."""
    acc = ""
    for i, tok in enumerate(tokens):
        if "," in tok:
            return None, 0
        acc += tok
        for k in NICK_KEYS:
            if acc.lower().endswith(k.lower()):
                return NICK[k], i + 1
    return None, 0


def cat(r):
    if r.startswith("Injury/Illness"):
        return "Illness" if r.endswith("- Illness") or "N/A; Illness" in r else "Injury"
    if r.startswith("Rest - "):
        return "Rest (injury management)"
    if r.startswith("Return to Competition"):
        return "Reconditioning"
    return None


def clean_injury(r):
    x = re.sub(r"^(Injury/Illness|Rest|Return to Competition Reconditioning)\s*-?\s*", "", r)
    return x.replace("N/A; ", "").replace("; ", " ").strip() or "Return to competition reconditioning"


def flip(n):
    n = n.strip()
    if "," in n:
        a, b = re.split(r",\s*", n, maxsplit=1)
        return f"{b} {a}".strip()
    return n


SKIP = re.compile(r"^(Injury Report:|Page \d+ of \d+|Game Date|Game Time|Matchup\b|Team\b|Player Name|Current Status|Reason\b)", re.I)
DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
TIME = re.compile(r"^\d{1,2}:\d{2}$")
MATCH = re.compile(r"^[A-Z]{2,3}@[A-Z]{2,3}$")


def parse(lines):
    """Returns (rows, teams_not_submitted). Each row is a dict with raw reason; carries date/time/matchup/team down the page."""
    rows, not_sub = [], []
    date = time_ = match = team = None
    for toks in lines:
        text = " ".join(toks)
        if not toks or SKIP.match(text):
            continue
        i = 0
        if i < len(toks) and DATE.match(toks[i]):
            date = datetime.strptime(toks[i], "%m/%d/%Y").strftime("%Y-%m-%d"); i += 1
        if i < len(toks) and TIME.match(toks[i]):
            time_ = toks[i]; i += 1
            if i < len(toks) and toks[i].strip("()").upper() in ("ET", "EST", "EDT"):
                i += 1
        if i < len(toks) and MATCH.match(toks[i]):
            match = toks[i]; i += 1
        t, used = team_of(toks[i:])
        if t:
            team = t; i += used
        rest = toks[i:]
        if not rest:
            continue
        if "NOT YET SUBMITTED" in " ".join(rest).upper():
            if team and team not in not_sub:
                not_sub.append(team)
            continue
        k = next((j for j, x in enumerate(rest) if x in STATUSES and j > 0 and "," in " ".join(rest[:j])), None)
        if k is not None:
            rows.append(dict(Team=team, Player=flip(" ".join(rest[:k])), Status=rest[k], reason=" ".join(rest[k + 1:]).strip(),
                             Game_Date=date, Matchup=match, Game_Time=time_))
        elif rows and not t and not DATE.match(toks[0]) and not any("," in x for x in rest):
            rows[-1]["reason"] = (rows[-1]["reason"] + " " + " ".join(rest)).strip()   # a reason that wrapped onto a second line
    return rows, not_sub


def read_prev(path):
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def main():
    now_et = datetime.now(ZoneInfo("America/New_York"))
    now_path, cur_path = "docs/now.json", "docs/current.csv"
    try:
        prev = json.load(open(now_path))
    except Exception:
        prev = {}

    def save_status(status, detail, extra=None):
        out = dict(prev); out.update(status=status, detail=detail)
        out.update(extra or {})
        if out != prev:                                   # only write (and so only commit) when something changed
            json.dump(out, open(now_path, "w"), indent=1)
        print(status, "-", detail)

    s, body, problem = find_latest(now_et)
    if not s:
        save_status("unreachable", problem); return
    if prev.get("status") == "ok" and prev.get("report_file") == s:
        print("Already up to date:", s); return
    try:
        lines = pdf_lines(body)
        try:
            dump_layout(body)
        except Exception as e:
            print("layout dump failed:", e)
        rows, not_sub = parse(lines)
    except Exception as e:
        rows, not_sub, lines = [], [], []
        problem = f"Could not open the report file ({type(e).__name__}: {e})."
    good = [r for r in rows if r["Team"] and r["Game_Date"]]
    if len(good) < 5:                                     # a real report always has dozens of rows
        os.makedirs("data", exist_ok=True)
        open("data/parse_failure.txt", "w").write(f"{s}\n" + "\n".join(" | ".join(l) for l in lines[:400]))
        save_status("parse_failed", problem or f"Read the report {s} but found only {len(good)} player rows.", dict(report_file_seen=s))
        return

    rtime = parse_stamp(s)
    report_et = rtime.strftime("%Y-%m-%d %H:%M")
    today = str(now_et.date())
    cur = []
    for r in good:
        c = cat(r["reason"])
        if c is None:                                     # G League, personal reasons, plain rest, etc.
            continue
        cur.append(dict(Team=r["Team"], Player=r["Player"], Status=r["Status"], Category=c, Injury=clean_injury(r["reason"]),
                        Game_Date=r["Game_Date"], Matchup=r["Matchup"] or "", Report_Time=report_et, Game_Time=r["Game_Time"] or ""))
    # each team's next game that has a filed report in this file
    first = {}
    for r in cur:
        if r["Game_Date"] >= today:
            first[r["Team"]] = min(first.get(r["Team"], r["Game_Date"]), r["Game_Date"])
    # a team whose only rows are all filtered out (nobody hurt) is still "filed"; remember who appeared at all
    seen = {r["Team"] for r in good}
    new = [r for r in cur if first.get(r["Team"]) == r["Game_Date"]]
    # keep older rows for teams this report doesn't cover yet, as long as their game hasn't passed
    keep = [r for r in read_prev(cur_path) if r.get("Team") not in seen and (r.get("Game_Date") or "") >= today]
    for r in keep:
        r.setdefault("Game_Time", "")
    allrows = sorted(new + keep, key=lambda r: (r["Team"], r["Player"]))
    with open(cur_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CUR_COLS); w.writeheader(); w.writerows(allrows)
    save_status("ok", f"Read {s}: {len(good)} player rows, {len(new)} kept for Right now.",
                dict(report_file=s, report_time_utc=rtime.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
                     report_time_et=report_et, rows=len(allrows), teams_not_submitted=not_sub))


if __name__ == "__main__":
    main()
