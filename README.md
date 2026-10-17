# 🇪🇬 Wealth Tracker

A personal financial tracker for EGP-based finances: loans *to* you, debts you owe, gold (18k/21k/24k) with live price fetching, USD, cash — plus monthly expense tracking with budgets, income, recurring bills and game'yat, savings goals, an income-vs-spending report, wealth-history snapshots with a chart, and CSV backup/restore.

Everything runs locally. Your data never leaves your machine.

---

## 🌐 Online edition — share it with anyone, no install

The same `finance.html` **works without server.py**. Open it on any static host (GitHub Pages, Netlify, even a USB stick) and it detects there's no backend, then runs everything **inside the visitor's browser**: data is saved in that browser's local storage, live gold prices still fetch (verified CORS-friendly), and CSV export/import works.

**Each person gets their own private data** — nothing is sent to any server, and people can't see each other's numbers. A badge at the top shows which edition is running: `☁️ Online edition` (browser storage) or `💾 Local server edition` (finance.db).

**Deploy it free on GitHub Pages (~3 minutes, no coding):**
1. Create a free account at [github.com](https://github.com), then create a new **public** repository (e.g. `wealth-tracker`) — no README needed.
2. Click **Add file → Upload files**, and drop in **`index.html`** from this folder.
3. Commit the upload.
4. Repo **Settings → Pages** → Source: **Deploy from a branch** → Branch: `main` / `root` → Save.
5. Wait ~1 minute — your app is live at `https://<your-username>.github.io/wealth-tracker/`. Share that link with anyone.

*(Netlify also works: drag the folder onto [app.netlify.com/drop](https://app.netlify.com/drop).)*

**After you edit `finance.html`**, run `make_online.bat` to refresh `index.html`, then upload it again (GitHub: open the file → pencil ✏️ → replace contents, or delete + re-upload).

**Moving data between devices or editions:** use ⬇️ **Export CSV** on the old device and 📂 **Import CSV** on the new one. Note the gold rate history isn't part of the CSV (it rebuilds from your next 🔄 live fetch).

⚠️ Browser-storage caveats: data lives in *that* browser on *that* device. Clearing site data / browsing history removes it, and incognito/private windows don't persist. Export a CSV backup regularly — it's your only copy.

---

## 🚀 Quick start

**Requirements:** [Python 3](https://www.python.org/downloads/) (3.10+) with the `flask` package:

```bash
python -m pip install flask
```

**Run it** — double-click **`start.bat`**, or from a terminal:

```bash
python server.py
```

The server starts at **http://127.0.0.1:8765** and your browser opens automatically. Keep the window open while using the app; close it (or press `Ctrl+C`) to stop.

---

## 📁 Files

| File | What it is |
|---|---|
| `server.py` | Flask backend + REST API (port 8765) — optional, for the local-server edition |
| `finance.html` | The whole app — one file, no build step, no CDN dependencies. Auto-detects: server.py running → server edition; static host → browser edition |
| `index.html` | Copy of `finance.html` for static hosting (GitHub Pages expects this name). Refresh it with `make_online.bat` |
| `finance.db` | **Your data** (server edition) — SQLite database, auto-created on first run |
| `start.bat` | Windows launcher (server edition) |
| `make_online.bat` | Refreshes `index.html` from `finance.html` before re-uploading |
| `README.md` | This file |

---

## ✨ Features & how to use them

### Wealth summary
The top card shows your **Total Current Wealth** plus the breakdown: Loans, Debts (subtracted), Gold, USD (amount × rate), and EGP cash. It updates live as you type. Net wealth = loans + gold + USD + cash **− debts**.

### 💰 Loans
People who owe you money. Add a name + amount (EGP). Every entry can be **edited** (✏️) or **deleted** (X) inline.

### 💳 Debts (money I owe)
Money that other people lent **you** — the mirror of Loans. Add who, how much, and an optional **pay-back date**. Debts are subtracted from your total wealth, and a row turns **⚠ overdue** (red) once its date passes without being repaid. Repay part of a debt by editing the amount; delete it when fully settled.

### 🥇 Gold
Add items with a name, weight in grams, and karat (18k / 21k / 24k). The value is `weight × karat price`. Set today's prices per gram in the three rate boxes — they **autosave** as you type, so they persist across restarts.

- **🔄 Live prices** fetches current market rates automatically: the gold spot price (USD/oz) and the USD→EGP exchange rate are combined into per-gram prices for 18k / 21k / 24k, filling the three rate boxes and saving them
- Every fetch (live or manual) is recorded, and a small **rate-history chart** shows how the 21k price moved over time — hover it to see all four rates on any recorded day

### 💵 USD & 💷 EGP
Enter your USD amount and the current exchange rate; the EGP field is your cash on hand. All three autosave.

### 💸 Monthly Costs & Income
- Log expenses with a **description, amount, category, and date**
- Categories: Food, Transport, Rent, Utilities, Phone/Internet, Health, Education, Family, Clothes, Fun, Other (free-typing your own also works)
- Navigate months with **◀ ▶** — each month shows its expense list, income, total, and net
- **Search & filter** the expense list by text or category
- **Category budgets**: type a monthly limit next to any category — the bar shows spent vs budget and turns **red** when you go over
- **Income**: log salary and other income per month (description, amount, source, date)
- **Recurring bills**: add a rule once (e.g. Rent on the 1st, Netflix on the 20th) — the expense is inserted automatically every month, marked with a 🔁 *auto* badge. Rules can be paused (⏸) or deleted; deleting an auto-inserted expense does **not** remove the rule
- A **yellow banner** warns you when an active bill is due within the next 7 days but hasn't been recorded yet
- The summary card shows your **Safe to Spend** for the current month: income minus each category's *max(spent, budget)* — an over-budget category counts at what you actually spent, so the number stays honest

### 🎯 Savings Goals
Add a goal with a target amount (and optional deadline). Each goal shows a progress bar and percentage — press **Save** to add money toward it (updates the bar). Goals can be edited or deleted inline.

### 📊 Income vs Spending
A grouped bar chart of the **last 6 months**: blue = income, orange = spending. Hover any month for exact figures and the net. Empty months simply show no bars.

### 🤝 Game'yat (جمعيات)
Track rotating savings groups: name, monthly amount, number of members, and your position. Each time you pay a round, press **+1 Round** — the progress bar fills toward `rounds done / total members`.

### 📈 Wealth History
- **📷 Save Snapshot** records the current totals (loans / debts / gold / USD / cash) with a timestamp
- The line chart plots your **grand total over time** — hover for a crosshair + tooltip, or focus the chart and use **← → arrow keys**
- Toggle **EGP / USD** above the chart to view your history in dollars (snapshots taken without a USD rate are skipped in USD mode)
- Take a snapshot whenever your wealth changes meaningfully (new loan, gold price move, payday) — the chart only grows when you feed it snapshots
- Each snapshot in the list can be deleted

### ⬇️📂 Backup & restore (CSV)
- **⬇️ Export CSV** downloads everything (loans, debts, gold, expenses, settings, snapshots, budgets, goals, income, recurring bills, game'yat) as one file: `wealth_export_YYYYMMDD_HHMM.csv`
- **📂 Import CSV** restores from such a file. ⚠️ **Import replaces all current data** — it behaves as a full backup/restore, and asks for confirmation first
- Imported recurring rules are automatically re-linked to their expenses, so auto-insertion never duplicates a bill after a restore
- Note: the gold **rate history** is not part of the CSV — after a restore, it rebuilds from your next 🔄 live-price fetch
- Keep exports somewhere safe (cloud drive, USB) — `finance.db` is the only other copy of your data

---

## 🗄️ For developers

### Database schema (`finance.db`)

```sql
loans           (id, name, amount, created_at)
debts           (id, name, amount, due_date NULL, created_at)
gold_items      (id, name, weight, karat CHECK 18|21|24, created_at)
expenses        (id, description, amount, category DEFAULT 'Other', spent_on DEFAULT today, recurring_id NULL, created_at)
incomes         (id, description, amount, source, received_on, created_at)
recurring       (id, description, amount, category, day_of_month, active, created_at)
recurring_skips (recurring_id, ym 'YYYY-MM', PK(recurring_id, ym))  -- months a deleted auto-expense must NOT reappear in
budgets         (category PRIMARY KEY, monthly_limit)
goals           (id, name, target_amount, saved_amount, deadline, created_at)
gameyat         (id, name, monthly_amount, total_members, my_position, rounds_done, created_at)
snapshots       (id, taken_at, total_loans, total_gold, total_usd, total_egp, total_debts, grand_total, usd_rate)
rate_history    (date PK, rate18k, rate21k, rate24k, usd_rate)
settings        (key PRIMARY KEY, value)   -- rate18k, rate21k, rate24k, usd_rate, usd_amount, egp_amount
```

Timestamps are local time. Totals in snapshots are computed **server-side** at save time — a snapshot is a frozen record, not a live query. `grand_total` = loans + gold + USD + cash **− debts**.

**Recurring bills** are materialized lazily: every `/api/state` call walks each active rule from its creation month to the current month and inserts any missing expense (one per rule per month, clamped to the month's length). Deleting such an expense writes a tombstone into `recurring_skips` so it is not re-inserted next time.

### REST API

| Method | Path | Body / notes |
|---|---|---|
| GET | `/api/state` | Everything: loans, debts, gold, settings, expenses, snapshots, totals, budgets, goals, incomes, recurring, gameyat, rate history, 6-month report |
| POST | `/api/loans` | `{name, amount}` |
| PUT / DELETE | `/api/loans/<id>` | `{name, amount}` |
| POST | `/api/debts` | `{name, amount, due_date?}` — date `YYYY-MM-DD`, optional |
| PUT / DELETE | `/api/debts/<id>` | `{name, amount, due_date?}` |
| POST | `/api/gold` | `{name, weight, karat}` — karat ∈ 18, 21, 24 |
| PUT / DELETE | `/api/gold/<id>` | `{name, weight, karat}` |
| PUT | `/api/settings` | any of the six keys above |
| POST | `/api/expenses` | `{description, amount, category?, date?}` — date `YYYY-MM-DD` |
| PUT / DELETE | `/api/expenses/<id>` | same fields |
| POST | `/api/incomes` | `{description, amount, source?, date?}` |
| DELETE | `/api/incomes/<id>` | |
| POST | `/api/recurring` | `{description, amount, category?, day_of_month}` — also materializes past-due months now |
| PUT / DELETE | `/api/recurring/<id>` | PUT `{active: false}` pauses/resumes a rule |
| POST | `/api/budgets` | `{category, monthly_limit}` — a limit of 0 removes the budget |
| POST / PUT / DELETE | `/api/goals[/<id>]` | `{name, target_amount, deadline?}`; PUT adds `{add_amount}` |
| POST | `/api/gameyat` | `{name, monthly_amount, total_members, my_position?}` |
| POST | `/api/gameyat/<id>/advance` | +1 round (capped at total_members) |
| PUT / DELETE | `/api/gameyat/<id>` | |
| POST | `/api/rates/fetch` | fetches live gold + USD→EGP rates, updates settings & rate history |
| POST | `/api/snapshots` | takes a snapshot of current totals |
| DELETE | `/api/snapshots/<id>` | |
| GET | `/api/export.csv` | download backup |
| POST | `/api/import` | multipart form, field `file` — replace-all restore |

Invalid input returns `400 {"error": "..."}`; a failed live-rate fetch returns `502`.

### CSV format

One header, typed rows — only the relevant columns are filled per type:

```csv
type,name,amount,weight,karat,category,date,total_loans,total_debts,total_gold,total_usd,total_egp,grand_total,saved_amount,day_of_month,active,members,position,rounds,usd_rate
loan,Ahmed,5000,,,,2026-10-15 19:52:43,,,,,,,,,,,,,
debt,Uncle Sameh,3000,,,,2026-11-01,,,,,,,,,,,,,
gold,Ring,,12.5,21,,2026-10-15 19:52:43,,,,,,,,,,,,,
expense,Bread,50,,,Food,2026-10-15,,,,,,,,,,,,,
setting,usd_rate,48.5,,,,,,,,,,,,,,,,,
budget,Food,500,,,,,,,,,,,,,,,,,
goal,New laptop,30000,,,,2027-06-30,,,,,,,12000,,,,,,
income,Salary,15000,,,October salary,2026-10-01,,,,,,,,,,,,,
recurring,Netflix,150,,,Fun,2026-10-17 10:00:00,,,,,,,,20,1,,,,
gameyat,Family gameya,1000,,,,,,,,,,,,,,10,4,2,
snapshot,,,,,,2026-10-15 19:52:44,5000,3000,40000,48500,20000,113500,,,,,,,48.5
```

For `budget` rows the **category goes in `name`** and the limit in `amount`; for `goal` rows `date` holds the deadline and `saved_amount` the money put aside so far; for `debt` rows `date` holds the pay-back date; for `recurring` rows `date` holds the rule's creation timestamp and `active` is 1/0.

---

## 🔧 Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'flask'` | `python -m pip install flask` |
| Port 8765 already in use | Another instance is running — close its window, or change `PORT` at the top of `server.py` |
| Browser didn't open | Manually go to http://127.0.0.1:8765 |
| Lost data / bad import | Restore your latest CSV export (📂 Import CSV) |
| Want to start over | Stop the server, delete `finance.db`, start again — a fresh empty database is created |
| Online edition: my data disappeared | Browser storage was cleared (or you switched browser/device/incognito). Restore your CSV export with 📂 Import CSV |
| Online edition: live prices don't fetch | The free rate APIs are occasionally down — type the rates manually; they autosave |
| Online edition shows `💾 Local server edition` | You have server.py running on the same address — that's fine, it just takes priority |
