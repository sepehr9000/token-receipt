# token-receipt

What would your Claude Code usage cost at Anthropic's API prices, and where does the money go? Run one line:

```sh
curl -fsSL https://raw.githubusercontent.com/sepehr9000/token-receipt/main/receipt.py | python3 -
```

Only the last 30 days:

```sh
curl -fsSL https://raw.githubusercontent.com/sepehr9000/token-receipt/main/receipt.py | python3 - --days 30
```

You need Python 3.8 or newer, which macOS and most Linux machines already have. On Windows, download `receipt.py` and run `py receipt.py`.

Options:

- `--days N` counts only the last N days.
- `--path DIR` also reads transcripts from another folder. Repeat it for more folders.

## What you get

Example output, from made-up transcripts:

```
Your Claude Code receipt, at Anthropic API list prices
Last 30 days: Oct 21, 2026 to Nov 18, 2026

  Total       $757.38
  Tokens      2.5B
  Requests    8,595

                     tokens   share           cost   share
  Cache reads          2.5B   98.6%        $446.03   58.9%
  Cache writes        28.1M    1.1%        $200.76   26.5%
  Uncached input      38.5K    0.0%          $0.14    0.0%
  Output               6.2M    0.2%        $110.46   14.6%

99% of your tokens were the model re-reading cached context.

By model: opus-5-5 $708.62 (94%), sonnet-5-5 $39.04 (5%), haiku-5-5 $9.72 (1%)
Prices from platform.claude.com/docs/en/about-claude/pricing, checked 2026-11-18.
Read 60 transcript files on this machine. Sent nothing anywhere.
```

The table splits your tokens and your cost four ways:

- **Cache reads** are context the model already saw on an earlier turn, read back from the prompt cache. They cost 2.5% to 10% of the input price, so they're most of your tokens and a smaller share of your cost.
- **Cache writes** are new context stored in the cache, at 1.25x the input price for the 5-minute cache or 2x for the 1-hour cache.
- **Uncached input** is input sent without the cache. Claude Code caches almost everything, so this is usually near zero.
- **Output** is everything the model wrote, thinking included.

## What it reads

- `~/.claude/projects/`: the transcript Claude Code writes for every session and subagent
- `$CLAUDE_CONFIG_DIR/projects/`, if you set that variable
- `~/Library/Application Support/Claude/local-agent-mode-sessions/`: sessions from the Claude desktop app on macOS
- any folder you pass with `--path`

From each response it keeps the model name, the time, and the token counts. It never prints your prompts, your code, or your file names.

Claude Code deletes transcripts older than 30 days unless you changed `cleanupPeriodDays`, so "everything" usually means about a month.

This reads Claude Code only. Codex and other agents log in other formats with other price lists, and I couldn't test them well enough to trust the number.

## It sends nothing anywhere

The script makes no network calls and imports only Python's standard library. It's one file you can read in five minutes. To check for yourself, download it, read it, and run it with your wifi off:

```sh
curl -fsSLO https://raw.githubusercontent.com/sepehr9000/token-receipt/main/receipt.py
python3 receipt.py
```

## How it counts

- **Each response once.** Claude Code writes one response across several lines, one per content block, and copies earlier responses into the new file when you resume a session. The script keys each response by `message.id` and `requestId` and counts it once. Some of those lines carry a partial output count, so it keeps the line with the largest count.
- **Cache writes** use the 5-minute or 1-hour price, from the split Claude Code records.
- **Fast mode** costs 2x. **US-only inference** costs 1.1x. **Web search** costs $10 per 1,000 searches.
- **Claude Haiku 5.5** prompts over 100,000 tokens use its higher price tier.
- **Fallbacks:** when one model declined a request and another finished it, each model's part is priced at its own rate.
- **Unknown models** are left out and named at the bottom of the receipt, so a new model never gets billed at an old model's price.

These are Claude API list prices. On a Pro or Max plan you didn't pay this amount; it's what the same tokens would cost on the API. Batch discounts and negotiated rates aren't included.

## Prices

Dollars per million tokens, from [platform.claude.com/docs/en/about-claude/pricing](https://platform.claude.com/docs/en/about-claude/pricing), checked November 18, 2026.

| Model | Input | 5m cache write | 1h cache write | Cache read | Output |
| :-- | --: | --: | --: | --: | --: |
| Claude Fable 5.2, Mythos 5.2 | $8 | $10 | $16 | $0.20 | $40 |
| Claude Fable 5.1, Mythos 5.1 | $10 | $12.50 | $20 | $0.25 | $50 |
| Claude Fable 5, Mythos 5 | $10 | $12.50 | $20 | $1 | $50 |
| Claude Opus 5.5 | $4 | $5 | $8 | $0.20 | $20 |
| Claude Opus 5, 4.8, 4.7, 4.6, 4.5 | $5 | $6.25 | $10 | $0.50 | $25 |
| Claude Opus 4.1, 4 | $15 | $18.75 | $30 | $1.50 | $75 |
| Claude Sonnet 5.5 | $2 | $2.50 | $4 | $0.10 | $10 |
| Claude Sonnet 5 | $2 | $2.50 | $4 | $0.20 | $10 |
| Claude Sonnet 4.6, 4.5, 4 | $3 | $3.75 | $6 | $0.30 | $15 |
| Claude Haiku 5.5, prompts up to 100K tokens | $0.10 | $0.125 | $0.20 | $0.01 | $0.50 |
| Claude Haiku 5.5, prompts over 100K tokens | $0.50 | $0.625 | $1 | $0.05 | $2.50 |
| Claude Haiku 4.5 | $1 | $1.25 | $2 | $0.10 | $5 |

When prices change, edit `PRICES` at the top of `receipt.py`.

## Tests

```sh
python3 test_receipt.py
```

The tests build made-up transcripts and check the receipt against dollar amounts worked out by hand.
