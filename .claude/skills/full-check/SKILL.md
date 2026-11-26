---
name: full-check
description: The regression and the walkthrough together. Runs the regression stages and the walkthrough crawl in parallel (separate databases, ports and folders), launches all five independent reviewers (three security, two walkthrough), publishes both private reports, logs both runs and gives the owner one summary. Fixes nothing in product code.
---

# /full-check

`/regression` and `/walkthrough` at once. Their skill files hold the details
(`.claude/skills/regression/SKILL.md`, `.claude/skills/walkthrough/SKILL.md`);
this file only says how they fit together.

## Steps

1. **Start both tracks** (with `run_in_background`):

   ```bash
   python3 regression/run.py --with-walkthrough
   ```

   One run folder (RUN). The regression track writes `RUN/regression/` and
   uses ports 18199–18202; the walkthrough track writes `RUN/walkthrough/` and
   uses ports 18299–18300. Each starts its own throwaway App with its own
   database, so they can't collide. The frontend is built once, before both.

2. **Launch the reviewers.**
   - Straight away, the three security reviewers (regression step 2:
     changes, claims, outsider).
   - As soon as `RUN/walkthrough/inventory.json` exists (watch for it with
     Monitor and an until-loop; the crawl takes a few minutes), the two
     walkthrough reviewers (walkthrough step 2: desktop, phone).

   All five run in parallel, each fresh, read-only, briefed with the code and
   material rather than conclusions, and each returns JSON in its schema.

3. **Wait** for the runner and all five reviewers. Save their results to
   `RUN/findings/security-*.json` and `RUN/findings/walkthrough-*.json`
   (failed reviewers recorded as failed).

4. **Render, publish both, log both:**

   ```bash
   python3 regression/run.py report RUN
   ```

   Publish `RUN/regression/report.html` and `RUN/walkthrough/report.html`
   to their links in `regression/published.yaml` (regression step 5,
   walkthrough step 3).

   ```bash
   python3 regression/run.py log RUN
   ```

   Commit `regression/RUNS.md`, `regression/WALKTHROUGHS.md`,
   `regression/ids/` and `regression/published.yaml` to the working branch
   and push.

5. **One summary for the owner** (start from
   `python3 regression/run.py summary RUN`):
   - totals per stage, failing check IDs;
   - security findings by severity, verified or not;
   - walkthrough UX failures and proposals by group;
   - what couldn't run and why;
   - both report links.

   Fix nothing unless the owner asks.
