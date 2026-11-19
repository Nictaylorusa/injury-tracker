# The Collective Analytics: NBA injury tracker

A website that keeps itself up to date. "Right now" refreshes about every 15 minutes from the NBA's own injury-report
files, and the five-season history rebuilds once a day. You do not need to write or run any code. Everything below is
clicking in a web browser.

## One-time setup (about 15 minutes)

1. **Make a free GitHub account** at github.com (skip if you have one).
2. Click the **+** at the top right, then **New repository**. Name it, for example, `injury-tracker`. Choose **Public**
   (free GitHub Pages needs a public repository). Click **Create repository**.
3. **Upload the files.** On the new repository page click **uploading an existing file**. Unzip the folder you were given
   and drag in these items: `build.py`, `fetch_now.py`, `requirements-now.txt`, `README.md`, and the two folders `docs`
   and `data`. Click **Commit changes**.
4. **Add the two schedule files.** Do this once for each file.
   Click **Add file, then Create new file**. In the name box type exactly `.github/workflows/update.yml`
   (typing the slashes makes the folders). Open `update.yml` from the unzipped folder in any text editor, copy all of it,
   paste it into the big box, and click **Commit changes**.
   Then repeat with the name `.github/workflows/update-now.yml` and the contents of `update-now.yml`.
5. **Allow it to save its updates.** Go to **Settings, then Actions, then General**. Scroll to **Workflow permissions**,
   choose **Read and write permissions**, and click **Save**.
6. **Turn on the website.** Go to **Settings, then Pages**. Under **Build and deployment** set **Source** to
   **Deploy from a branch**, **Branch** to `main` and the folder to `/docs`. Click **Save**.
7. **Run both updates once.** Click the **Actions** tab. Choose **Update injury data** on the left, click **Run workflow**,
   then the green button. When it finishes (a couple of minutes), do the same for **Update Right now**.
   A green check means it worked.
8. **Check the live feed.** Open the **Update Right now** run and click into it. The last lines say either
   `ok - Read 2026-..._PM: ... player rows` (working) or `unreachable` / `parse_failed` (see "What could break").
9. **Open your site.** Go back to **Settings, then Pages**. After a minute the address appears at the top
   (it looks like `https://your-name.github.io/injury-tracker/`). That is your link. Share it with anyone.

## Using your own web address (optional)
Buy or use a domain you own, for example `injuries.thecollectiveanalytics.com`. In **Settings, then Pages** type it into
**Custom domain** and follow the on-screen note about the DNS record to add at your domain provider (your domain
provider's help pages explain how to add a CNAME record).

## What updates and when
- **Right now:** about every 15 minutes, day and night, the site looks for the newest injury-report file on the NBA's
  server and reads it. The top of the tab says which report it shows and how old it is. GitHub often starts scheduled
  runs a few minutes late, so expect "about 15 minutes", not exactly. A page that is already open checks for newer numbers
  every 5 minutes.
- **History (everything except Right now):** once a day, rebuilt from the public archive of the NBA's reports. The archive
  can run a day or more behind, so the newest games may take a day to show up in the history.
- Past seasons (2022-23 to 2025-26) are saved in `data/stays_past.csv` and do not change. When a season ends, the next
  daily update saves it automatically.
- Go to the **Actions** tab any time to see the last runs. A run only saves something when the numbers changed.

## What could break
- **The NBA's server may refuse GitHub's computers.** This was not possible to test in advance. If it happens, the page
  says "Live feed paused" and keeps the last good numbers. The log of the run says `unreachable`.
- **The NBA could change the layout of its report.** The log then says `parse_failed` and a file `data/parse_failure.txt`
  appears with what the script saw. Send that file to Claude and ask for a fix to `fetch_now.py`.
- If the archive changes its file names or columns, the daily update fails and the site keeps its last history.
  Ask Claude to fix `build.py` and paste in the error text from the failed run.
- GitHub may pause scheduled runs on a repository with no activity for 60 days. Press **Run workflow** to restart.

## Data and terms
Right now comes from the NBA's official injury reports. History comes from the same reports as republished by the
sportsdataverse project. The repository and site are public, so anyone can download the data files. Check the NBA's and
sportsdataverse's terms before using it commercially.
