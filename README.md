# NS Financial Services: Mutual fund explorer

A dashboard of every open-ended mutual fund in India (regular plans only), grouped by SEBI category, built from AMFI's and the fund houses' own data and refreshed automatically every night.

- **Top 10 per category**, ranked by the measure the visitor picks: rolling consistency, smallest falls, long-term return, risk-adjusted return, or their own weights. "Show all" lists every fund in the category, sortable by any column.
- **Simple view** (default) with plain-language columns, a "how to read" guide, a one-line description of every fund type and an "In plain words" summary on each fund page; **Detailed view** for experienced investors.
- **Fund type dropdown** (Large cap, Multi cap, Liquid ...) to jump straight to one category.
- **Regular plans only**, a **fund house (AMC) filter**, and a fund house view that shows each AMC's schemes with their rank in their own category.
- **Rolling returns everywhere**: typical 1, 3, 5, 7 and 10-year rolling returns as table columns (sortable), plus the rolling-return chart on each fund page. New ranking: **Risk-adjusted return**.
- **Key ratios** (3 and 5 years, from month-end NAVs): standard deviation, Sharpe, Sortino, beta, alpha, R-squared, up/down capture, tracking error, information ratio, next to the category's typical fund.
- **AMFI risk check** for mid and small cap funds: days to sell 25% / 50% of the portfolio, top 10 investors' share, PE vs benchmark, turnover (AMFI's monthly disclosure).
- **Latest portfolio** from each fund house's monthly file: asset mix, top holdings, sectors, large/mid/small cap split (AMFI list), credit quality and yield for debt, and **what changed since last month** by number of shares held.
- **Portfolio overlap** between funds in Compare.
- **Fund page**: rolling returns (1, 3, 5, 7, 10 years) vs category and a matching index fund, chance of loss by holding period, pain meter (worst fall and recovery), crash replay, SIP range across every start month, money in hand after tax and inflation, year-by-year quartiles, NAV history.
- **Compare** up to 4 funds side by side.

Data sources: AMFI (NAV files, NAV history, TER, average AUM, fund performance, risk parameters, market-cap list) and each fund house's own monthly portfolio file. Nothing comes from third-party sites.

---

## One-time setup (about 15 minutes)

### 1. Create a free GitHub account
Go to https://github.com/signup and sign up. Use your business email if you like.

### 2. Create a repository
1. Click **+** (top right) → **New repository**.
2. Name: `ns-fund-explorer`. Choose **Public** (free GitHub Pages hosting needs a public repository; there are no passwords or private data in these files).
3. Click **Create repository**.

### 3. Upload the files
1. On the new repository page, click **uploading an existing file**.
2. Unzip `ns-fund-explorer.zip` on your computer. Open the unzipped folder, select **everything inside it** and drag it into the GitHub page.
3. Click **Commit changes**.
4. Check that a folder named `.github` now appears in the repository. On some computers it is hidden and does not upload. If it is missing:
   - Click **Add file → Create new file**.
   - In the name box type `.github/workflows/update.yml`.
   - Open `github-workflows-copy/update.yml` from the unzipped folder, copy everything and paste it in.
   - Click **Commit changes**. Do the same for `check-amfi.yml` and `check-portfolios.yml`.

### 4. Turn on GitHub Pages
**Settings → Pages → Build and deployment → Source: GitHub Actions.**

### 5. Run it for the first time
1. Open the **Actions** tab. If GitHub asks, click the button to enable workflows.
2. Click **Update fund data** → **Run workflow** → **Run workflow**.
3. The first run downloads up to 20 years of NAV history from AMFI, newest years first. It can take up to 5 hours. If it runs out of time, the next night's run continues from where it stopped, and the dashboard is published at the end of every run.
4. When it finishes, the dashboard is live at `https://<your-github-username>.github.io/ns-fund-explorer/`.

### 6. Put it on your own domain: funds.nsfinancialservices.co.in
1. In GitHub: **Settings → Pages → Custom domain** → type `funds.nsfinancialservices.co.in` → **Save**.
2. In GoDaddy: **My Products → nsfinancialservices.co.in → DNS → Add new record**
   - Type: **CNAME**
   - Name: **funds**
   - Value: **`<your-github-username>.github.io`**
   - TTL: 1 hour
3. Back in GitHub Pages settings, wait for the DNS check to pass (can take up to an hour), then tick **Enforce HTTPS**.

Your main website on Netlify is not touched. Add a "Fund explorer" link on it pointing to `https://funds.nsfinancialservices.co.in`.

---

## After setup

- It updates itself twice a night (01:10 and 07:10 IST). Nothing to do.
- Once a week it saves a one-line `last-update.txt` file. This keeps GitHub's nightly schedule switched on (GitHub pauses schedules in repositories with no activity for 60 days).
- To force an update: **Actions → Update fund data → Run workflow**.

## Monthly portfolios

Fund houses publish last month's portfolio by the 10th. Every night the job checks which fund houses still need it, opens their portfolio pages with a small headless browser, and downloads the file. Once a fund house's file is in, it is skipped until the next month, so most nights this takes a minute.

- **Actions → Check portfolio sources → Run workflow** shows what the robot finds on every fund house page (you can type one fund house, e.g. `hdfc`). Send the output to Claude if a fund house is missing.
- If a fund house's site blocks the robot, download its file yourself and upload it to the **`portfolios/inbox`** folder (instructions are in that folder). The next run reads it.
- The list of portfolio pages is in `scripts/portfolio_sources.json` (taken from AMFI's Portfolio Disclosure page).

## If something goes wrong

- **Actions → Check AMFI sources → Run workflow** tries every AMFI address and prints what came back. Open the run, copy the output and send it to Claude.
- If a nightly run fails, the previous dashboard stays online.
- If AMFI changes its website, only `scripts/amfi_fetch.py` needs updating.

## What's inside

| Path | What it does |
|---|---|
| `site/index.html` | The dashboard page |
| `scripts/amfi_fetch.py` | Downloads data from AMFI (latest NAVs, NAV history, TER, AUM, benchmark returns) |
| `scripts/build_data.py` | Calculates every measure and writes `site/data/` |
| `scripts/common.py` | Reads AMFI files, sorts schemes into categories and plans |
| `scripts/make_sample.py` | Makes the clearly labelled sample data used only for the preview |
| `scripts/make_sample_extras.py` | Sample portfolios and risk data for the preview (fictional companies) |
| `scripts/ratios.py` | Key ratios from month-end NAVs |
| `scripts/amfi_risk.py` | AMFI risk parameters for mid and small cap funds |
| `scripts/portfolio_fetch.py` | Downloads fund houses' monthly portfolio files and AMFI's market-cap list |
| `scripts/portfolio_parse.py` | Reads the portfolio spreadsheets (any SEBI-format file, zips too) |
| `scripts/portfolio_build.py` | Matches portfolios to funds and works out mix, sectors, caps and changes |
| `scripts/namematch.py` | Matches scheme names in AMC files to AMFI's names |
| `portfolios/inbox/` | Drop a portfolio file here if the robot can't download it |
| `.github/workflows/update.yml` | The nightly job |
| `.github/workflows/check-amfi.yml` | A manual check of AMFI's sources |
| `.github/workflows/check-portfolios.yml` | A manual check of the fund houses' portfolio pages |

Try it on your own computer (needs Python 3.10+; `pip install -r requirements.txt`):

```
python scripts/make_sample.py      # sample data, or: python scripts/amfi_fetch.py daily
python scripts/make_sample_extras.py
python scripts/build_data.py
cd site && python -m http.server 8000   # then open http://localhost:8000
```

---

Mutual Fund investments are subject to market risks, read all scheme related documents carefully. Past performance may or may not be sustained in future. The dashboard shows how funds compare on past data; it is not investment advice.

NS Financial Services · AMFI Registered Mutual Fund Distributor · ARN-347748
