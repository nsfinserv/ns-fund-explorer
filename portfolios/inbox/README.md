# Portfolio inbox

If the nightly robot cannot download a fund house's monthly portfolio (its site blocks robots, or the page needs clicks the robot can't do), put the file here:

1. Download the monthly portfolio Excel or zip from the fund house's website (or from AMFI's Portfolio Disclosure page).
2. In GitHub, open this `portfolios/inbox` folder → **Add file → Upload files** → drop the file in → **Commit changes**.
3. The next nightly run reads it. The scheme names inside the file are matched to AMFI's list automatically, and the month comes from the "as on" date in the file.

Old files can stay; each month is stored separately. Delete them whenever you like.
