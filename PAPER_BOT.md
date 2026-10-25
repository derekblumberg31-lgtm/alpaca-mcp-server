# No-cost paper account observer

The GitHub Actions workflow runs a paper-account research cycle every 30 minutes
at :07 and :37 UTC. It stays **read-only** until the owner supplies paper keys,
configures a verified email route, and explicitly sets
`PAPER_AUTO_TRADING_ENABLED=true`. Even then it can submit at most one bounded
paper buy per run; it never uses a live-trading endpoint. It cannot send
messages to Codex. No credentials belong in the repository or its logs.

GitHub's standard runner is free for public repositories, but scheduled runs
can be delayed or dropped, and schedules in public repositories stop after
60 days without repository activity. This is not a continuous 24/7 process or
a reliable emergency trading control. The existing Render Free web service
also sleeps, and no paid plan has been authorized.

## Activation, only after separate credential approval

1. The observer code and workflow are published to the fork's default branch.
   Enable Actions for that fork if GitHub asks.
2. The GitHub Actions environment `paper-observer` has been created and
   restricted to `main`. The owner still needs to enter
   `ALPACA_PAPER_API_KEY` and `ALPACA_PAPER_SECRET_KEY` as **environment
   secrets**, not repository secrets. This limits the keys to jobs that
   reference that environment. Do not put the values in chat, code,
   workflow files, variables, or logs.
3. Trigger the workflow once manually in observation mode and confirm it
   succeeds without exposing account details in the public log.

## Free email reporting and paper-order enablement

Resend currently has a free tier of 3,000 transactional emails per month,
subject to its limits. The owner must create or sign into a Resend account,
configure a sender that Resend permits to reach the intended recipient, and
create a **sending-only** API key. A verified domain may be necessary; the
`resend.dev` testing sender has recipient restrictions. Do not enable trading
until a test email reaches the intended inbox. The owner should enter these in
the `paper-observer` GitHub Actions environment:

- Environment secrets: `RESEND_API_KEY` and `PAPER_BOT_EMAIL_TO`, in addition
  to the two Alpaca paper secrets above.
- Environment variable: `PAPER_BOT_FROM_EMAIL`, using the authorized sender.
- Environment variable: `PAPER_AUTO_TRADING_ENABLED` remains unset or `false`
  until the email test and a read-only account check both succeed. The owner
  must set it to `true` to authorize unattended paper orders. Set it back to
  `false` to stop future orders; cancel any existing open order separately.

The bot requires an accepted email API request **before** each paper order.
It then reports order acceptance, fills on the next scheduled check, and a
daily balance. Failed reporting blocks new orders. Email delivery and GitHub
schedule timing are not guaranteed; check both systems regularly. Resend will
process the balance and trade details contained in these messages.

The workflow invokes `--mode observe` by default. Only when the explicit
enable variable and all reporting settings are present does it invoke
`--mode trade`. In-chat delivery is unavailable from GitHub Actions.

## Pilot rules, for review only

The proposed strategy considers SPY, QQQ, IWM, and TLT. It checks 200
completed IEX daily bars, 50- and 200-day averages, 63-session momentum,
20-session volatility, a recent quote and spread, and recent ETF news. It
skips missing research, unexpected holdings, open orders, non-market hours,
and a one-month equity drawdown of at least 10%. Potential buys use cash,
not margin, keep a 20% cash floor, and cap a position at 40% of equity.
The prototype only considers buys, not sells or day trades, and cannot
guarantee a profit. Its default read-only cycle does not execute proposals.

The existing Codex chat monitor can report while the local app is able to
run, but it is not an always-available cloud messaging endpoint. A chat word
is not a remote kill switch for an independent process. To stop future
orders, set `PAPER_AUTO_TRADING_ENABLED=false` in GitHub or disable the workflow.
Neither action cancels an order already accepted by Alpaca.
