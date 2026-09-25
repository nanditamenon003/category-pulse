# Case study: Category Pulse

*A prototype built from what I saw on a retail floor. All figures below come from the project's simulated store month, not from real store data.*

---

## 1. The real-world problem

A fashion store sells across dozens of categories (polos, chinos, women's knit tops, kids' denim), and each one has a monthly unit target. On the floor I worked on, progress was tracked by downloading a sales report from the store's system into a spreadsheet: last year's units, this month's target, units sold so far, and "balance to do".

The store's system had the data. What was missing was the last step: turning numbers into decisions while there was still time to act.

- **It happened too late.** The sheet was built at the end of the day, after the selling hours were gone.
- **"Balance to do" ignored the calendar.** A gap of 166 units sounds worse than a gap of 52. But in the simulated month, the category with the −166 gap (THM Polo) is *on pace* and will finish fine, while the −52 one (THM chinos) is 19% behind and in real trouble. Without knowing how much of the month is left, a raw gap ranks problems in the wrong order.
- **The "why" was guesswork.** A category falling behind could mean fewer shoppers, an empty shelf, missing sizes or poor service, and each needs a different fix.
- **Some problems never showed in the totals at all.** A category can hold plenty of stock while the sizes most people wear have run out. The total looks healthy, so nobody checks the sizes, and the category quietly stops selling.

## 2. What Category Pulse does

Category Pulse sits on top of the store's existing system and answers four questions a floor manager actually has:

1. **Is each category on track?** Month-to-date pace for every category, shown as a status in words (Behind, Drifting, On pace, Ahead, Too early), along with how many units a day it now needs vs what it's actually selling.
2. **What needs action right now?** Alerts for stockouts, broken size runs, last pieces on the shelf, and sizes likely to run out before the next delivery.
3. **Why is it behind, and what do we do?** For each struggling category, one likely cause with the evidence behind it, and one concrete action. For example:
   > *THM Non Denim Bottom is 19% behind and has a broken size run: plenty on the shelf, but not the sizes most people need. Tomorrow: request a transfer of sizes 32 and 34 from a nearby store (the last delivery, on day 18, came without them).*
4. **How do we win back sales?** Cross-sell ideas aimed at the loyalty tier most likely to respond, tomorrow's staffing by floor zone, and a short end-of-day summary that replaces the evening spreadsheet.

A manager can also just ask a question. An AI assistant answers it by looking up the real numbers, and a "How I got this" panel shows every lookup it made.

## 3. How it works (in plain terms)

- **A simulated store month.** A generator creates 24 days of hourly sales, stock, deliveries and visitor counts for a store with 29 categories. Sales can only happen when the size is on the shelf, so the planted problems (a missed delivery, a warehouse short of core waist sizes) *cause* stockouts and broken size runs rather than having them written in by hand.
- **Rules that do the analysis.** Small, readable functions calculate pace, check stock health, trace deliveries, compare visitors with buyers, find peak hours and suggest cross-sells. All the tunable business rules (targets, the 15% threshold, stock rules) live in one settings file.
- **An AI assistant with tools.** The assistant can't see the data directly. It has to call those same functions ("tools") to get numbers, and its instructions forbid estimating. That's what keeps its answers grounded.
- **A dashboard for the floor.** A single web page built for a phone: status first, alerts at the top, a slider to step through the day, the chat, and the summary.

## 4. Key design decisions

**Month-to-date, not hour-by-hour.** The original idea tracked each category hour by hour against a daily target. Real targets are monthly, though, and many categories sell only a few units a day, so hourly pace per category is mostly noise. Pace is tracked month-to-date. Hour-by-hour tracking is kept only for whole lines (e.g. all of womenswear), where volumes are large enough to mean something.

**The 15% threshold, plus a check for normal ups and downs.** A category is "behind" when it's more than 15% under pace. That's a business rule, chosen because a category more than 15% behind late in the month rarely recovers without intervention. But at small volumes, 15% can be pure chance: 0 sold against 1.8 expected reads as "−100%". So the gap must *also* be bigger than normal randomness to count as behind. A category past the line but not clearly beyond chance is shown as "Drifting" (amber): worth watching, not yet proven. This stopped the dashboard from turning red every morning.

**A broken size run is its own cause.** "Out of stock" and "the core sizes are gone" need different responses: chase the missing delivery vs transfer specific sizes. Category Pulse compares each category's stock with its *own usual level*. That's how it catches the chinos case: 60 units on the shelf, more than usual, yet no 32s or 34s since day 16.

**Traffic vs conversion.** If fewer people come in, it's a footfall or marketing problem. If the usual number come in but fewer buy, it's stock, sizes, price or service. For Womens Knit Top, visitors over the last few days matched a typical stretch exactly (78 vs 78), but the share who bought fell from 20.6% to 11.5%. The shelf was empty.

**Privacy: loyalty targeting stays at tier level.** Cross-sell ideas target loyalty tiers (Platinum, Gold, Silver, Non-member), never individual customers. The data holds only tier summaries, such as how often each tier takes up a cross-sell and which offer it prefers. This is deliberate: it avoids profiling individual shoppers.

**Simulated data, on purpose.** Using real store data would need the store's approval, so the prototype runs on a realistic simulation instead. It keeps real proportions (category mix, monthly targets set from last year plus growth, weekend peaks, a sale day) but uses no real figures, and it regenerates identically every time.

**No machine-learning model, yet.** A trained forecasting model sounds impressive. But on simulated data it would only re-learn the simulator's own rules, and one store's month is far too little data for a model to beat simple methods. So the projections are plain and explained ("if the current rate continues...") and always labelled. When I checked this simple approach against a real month from my internship, most categories landed within about ±8% of their actual month-end. Machine learning is future work, only with real data, and only if it beats that simple baseline.

**A layer on top, not a new system.** The store already has a system that records every sale and stock movement. Category Pulse doesn't replace it or ask anyone to type data in. The next step would be to read the report the manager already downloads.

**A swappable AI provider.** The assistant is written against one standard interface, and the provider is a single setting. The prototype runs on DeepSeek, which was affordable for a demo: eight test questions cost about one US cent. It can switch to Claude, as originally specified, or to whichever provider a company approves, by changing one line.

## 5. Potential impact if deployed

In the simulated month, the value is mostly **time**:

- **Chinos:** the core waist sizes ran out on day 16. On day 17 the category still looked "on pace" (−11%); it only crossed into "behind" in the last week. The broken-size-run alert would have flagged it about a week earlier, while a size transfer could still have saved the month.
- **Womens Knit Top:** the scheduled delivery on day 18 never arrived, and the shelf was empty by day 23. On day 17 the category was on pace. A "scheduled delivery not received" check on day 18 gives five days' warning before the stockout.
- **Daily routine:** the end-of-day spreadsheet becomes a short summary that's already calculated, already checked (the contribution report verifies its own totals, so a broken formula can't silently drop rows), and already explained.

What a real pilot would need:
- the manager's and head office's approval to use real data
- an AI provider the company approves
- a way to read the store's existing report exports

What would prove the idea:
- fewer lost sales from stockouts and broken size runs
- problems caught days earlier
- managers acting on the summary instead of rebuilding it every evening
