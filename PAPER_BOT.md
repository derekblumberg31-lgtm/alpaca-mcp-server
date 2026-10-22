# No-cost paper account observer

The prepared GitHub Actions workflow runs a **read-only** paper-account research
cycle every 30 minutes at :07 and :37 UTC. It does not place orders, move money,
or send messages to Codex. It remains inactive until the owner separately
approves adding paper-account credentials as GitHub Actions secrets. No
credentials belong in the repository or its logs.

GitHub's standard runner is free for public repositories, but scheduled runs
can be delayed or dropped, and schedules in public repositories stop after
60 days without repository activity. This is not a continuous 24/7 process or
a reliable emergency trading control. The existing Render Free web service
also sleeps, and no paid plan has been authorized.

## Activation, only after separate credential approval

1. Publish the reviewed code to the fork's default branch and enable Actions
   for that fork if GitHub asks. Neither step has happened yet.
2. The GitHub Actions environment `paper-observer` has been created and
   restricted to `main`. The owner still needs to enter
   `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_SECRET_KEY` as **environment
   secrets**, not repository secrets. This limits the keys to jobs that
   reference that environment. Do not put the values in chat, code,
   workflow files, variables, or logs.
3. Trigger `Paper account observation` once manually and confirm that the
   workflow succeeds without exposing account details in the public log.

The workflow invokes `python -m alpaca_mcp_server.paper_bot --mode observe
--quiet`. The module has a separate trade implementation, but the workflow
hard-codes observation mode. Automatic orders are **not enabled**. The
one-shot trade mode refuses to run without a verified reporting channel;
in-chat delivery is not currently available from GitHub Actions. The user
chose in-chat updates, so no email service is configured.

## Pilot rules, for review only

The proposed strategy considers SPY, QQQ, IWM, and TLT. It checks 200
completed IEX daily bars, 50- and 200-day averages, 63-session momentum,
20-session volatility, a recent quote and spread, and recent ETF news. It
skips missing research, unexpected holdings, open orders, non-market hours,
and a one-month equity drawdown of at least 10%. Potential buys use cash,
not margin, keep a 20% cash floor, and cap a position at 40% of equity.
The prototype only proposes buys, not sells or day trades, and cannot
guarantee a profit. Its read-only scheduled cycle does not execute proposals.

The existing Codex chat monitor can report while the local app is able to
run, but it is not an always-available cloud messaging endpoint. A chat word
is not a remote kill switch for an independent process. To stop future
scheduled observations, disable this workflow in GitHub Actions; because
the workflow is read-only, there are no autonomous orders to stop.
