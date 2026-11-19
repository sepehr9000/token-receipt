#!/usr/bin/env python3
"""token-receipt: what your Claude Code usage costs at Anthropic API list prices.

Reads the transcripts Claude Code keeps on this machine and prints one receipt.
Sends nothing anywhere. Python 3.8+ standard library only.

    python3 receipt.py              # every transcript on this machine
    python3 receipt.py --days 30    # only the last 30 days
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

PRICES_CHECKED = "2026-11-18"
PRICES_SOURCE = "platform.claude.com/docs/en/about-claude/pricing"

# Anthropic API list prices, $ per million tokens: (input, output, cache read).
# Every model here bills cache writes at 1.25x input (5-minute cache) and 2x input (1-hour cache).
PRICES = {
    "claude-fable-5-2": (8.00, 40.00, 0.20),
    "claude-mythos-5-2": (8.00, 40.00, 0.20),
    "claude-fable-5-1": (10.00, 50.00, 0.25),
    "claude-mythos-5-1": (10.00, 50.00, 0.25),
    "claude-fable-5": (10.00, 50.00, 1.00),
    "claude-mythos-5": (10.00, 50.00, 1.00),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-opus-4-7": (5.00, 25.00, 0.50),
    "claude-opus-4-6": (5.00, 25.00, 0.50),
    "claude-opus-4-5": (5.00, 25.00, 0.50),
    "claude-opus-4-1": (15.00, 75.00, 1.50),
    "claude-opus-4": (15.00, 75.00, 1.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-sonnet-4-6": (3.00, 15.00, 0.30),
    "claude-sonnet-4-5": (3.00, 15.00, 0.30),
    "claude-sonnet-4": (3.00, 15.00, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}
# Claude Haiku 5.5 bills a request whose prompt (input + cache reads + cache writes) is over 100,000 tokens at these.
HAIKU_55_LONG = (0.50, 2.50, 0.05)
HAIKU_55_LONG_OVER = 100000
CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0
FAST_MODE = 2.0  # usage.speed == "fast": every token type costs 2x
US_ONLY = 1.1  # usage.inference_geo == "us": every token type costs 1.1x
WEB_SEARCH = 10.00 / 1000  # $ per search

TYPES = [
    ("cache_read", "Cache reads"),
    ("cache_write", "Cache writes"),
    ("input", "Uncached input"),
    ("output", "Output"),
]


def transcript_roots(extra):
    home = os.path.expanduser("~")
    patterns = [os.path.join(home, ".claude", "projects")]
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        patterns.append(os.path.join(os.path.expanduser(config_dir), "projects"))
    # Claude desktop app sessions on macOS.
    patterns.append(os.path.join(home, "Library", "Application Support", "Claude",
                                 "local-agent-mode-sessions", "**", ".claude", "projects"))
    patterns += [os.path.expanduser(p) for p in extra]
    roots = []
    for pattern in patterns:
        for root in sorted(glob.glob(pattern, recursive=True)):
            root = os.path.realpath(root)
            if os.path.isdir(root) and root not in roots:
                roots.append(root)
    return patterns, roots


def transcript_files(roots):
    seen = set()
    files = []
    for root in roots:
        for path in sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)):
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                files.append(real)
    return files


def parse_time(value):
    if not isinstance(value, str) or len(value) < 19:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            when = datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when


def model_key(model):
    """'claude-opus-4-5-20251101', 'us.anthropic.claude-opus-4-5-20251101-v1:0', 'claude-opus-4-1@20250805',
    'claude-sonnet-4-5[1m]' -> the price-table key."""
    found = re.search(r"claude-[a-z0-9.-]+", model.lower())
    if not found:
        return model
    key = re.sub(r"-v\d+$", "", found.group(0))
    return re.sub(r"-\d{8}$", "", key)


def num(value):
    return value if isinstance(value, (int, float)) and value > 0 else 0


def token_counts(usage):
    """(uncached input, output, cache read, 5-minute cache write, 1-hour cache write)."""
    written = num(usage.get("cache_creation_input_tokens"))
    split = usage.get("cache_creation")
    split = split if isinstance(split, dict) else {}
    w5 = num(split.get("ephemeral_5m_input_tokens"))
    w1 = num(split.get("ephemeral_1h_input_tokens"))
    if written > w5 + w1:  # no TTL split recorded: bill the rest as 5-minute writes
        w5 += written - w5 - w1
    return (num(usage.get("input_tokens")), num(usage.get("output_tokens")),
            num(usage.get("cache_read_input_tokens")), w5, w1)


def read_file(path, cutoff):
    """One record per API response in this file. Claude Code writes a response on several lines (one per
    content block), and some carry a partial output count, so keep the line with the largest one."""
    best = {}
    try:
        handle = open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        return best
    with handle:
        for n, line in enumerate(handle):
            if '"usage"' not in line or '"assistant"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict) or entry.get("type") != "assistant":
                continue
            message = entry.get("message")
            if not isinstance(message, dict):
                continue
            usage = message.get("usage")
            model = message.get("model")
            if not isinstance(usage, dict) or not isinstance(model, str) or not model or model == "<synthetic>":
                continue
            when = parse_time(entry.get("timestamp"))
            if when is None or (cutoff is not None and when < cutoff):
                continue
            key = (message.get("id"), entry.get("requestId"))
            if not key[0]:
                key = (path, n)
            output = num(usage.get("output_tokens"))
            if key not in best or output > best[key][0]:
                best[key] = (output, model, when, usage)
    return best


class Receipt:
    def __init__(self):
        self.tokens = dict((t, 0) for t, _ in TYPES)
        self.cost = dict((t, 0.0) for t, _ in TYPES)
        self.searches = 0
        self.requests = 0
        self.by_model = {}
        self.unpriced = {}
        self.first = None
        self.last = None

    def add(self, model, when, usage):
        multiplier = FAST_MODE if usage.get("speed") == "fast" else 1.0
        if usage.get("inference_geo") == "us":
            multiplier *= US_ONLY
        # A request that fell back to another model lists each model's part under "iterations".
        parts = [(model, usage)]
        iterations = usage.get("iterations")
        if isinstance(iterations, list) and len(iterations) > 1:
            parts = [(it.get("model") or model, it) for it in iterations if isinstance(it, dict)]
        rows = []
        for part_model, part in parts:
            key = model_key(part_model if isinstance(part_model, str) else model)
            inp, out, read, w5, w1 = token_counts(part)
            prices = PRICES.get(key)
            if prices is None:
                self.unpriced[key] = self.unpriced.get(key, 0) + 1
                return
            if key == "claude-haiku-5-5" and inp + read + w5 + w1 > HAIKU_55_LONG_OVER:
                prices = HAIKU_55_LONG
            p_in, p_out, p_read = prices
            m = multiplier / 1e6
            rows.append((key, {
                "input": (inp, inp * p_in * m),
                "output": (out, out * p_out * m),
                "cache_read": (read, read * p_read * m),
                "cache_write": (w5 + w1, (w5 * CACHE_WRITE_5M + w1 * CACHE_WRITE_1H) * p_in * m),
            }))
        tools = usage.get("server_tool_use")
        searches = num(tools.get("web_search_requests")) if isinstance(tools, dict) else 0
        for key, row in rows:
            for kind, (count, dollars) in row.items():
                self.tokens[kind] += count
                self.cost[kind] += dollars
                self.by_model[key] = self.by_model.get(key, 0.0) + dollars
        self.searches += searches
        self.by_model[model_key(model)] = self.by_model.get(model_key(model), 0.0) + searches * WEB_SEARCH
        self.requests += 1
        self.first = when if self.first is None or when < self.first else self.first
        self.last = when if self.last is None or when > self.last else self.last

    def total_cost(self):
        return sum(self.cost.values()) + self.searches * WEB_SEARCH

    def total_tokens(self):
        return sum(self.tokens.values())


def short_tokens(n):
    for size, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= size:
            return "%.1f%s" % (n / size, suffix)
    return str(int(n))


def money(x):
    return "${:,.2f}".format(x)


def pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


def plain_pct(value):
    return ("%.0f%%" % value) if 1 <= value < 99.5 else ("%.1f%%" % value)


def day(when):
    local = when.astimezone()
    return "%s %d, %d" % (local.strftime("%b"), local.day, local.year)


def tilde(path):
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path.startswith(home) else path


def main():
    parser = argparse.ArgumentParser(description="What your Claude Code usage costs at Anthropic API list prices.")
    parser.add_argument("--days", type=int, metavar="N", help="only count the last N days")
    parser.add_argument("--path", action="append", default=[], metavar="DIR",
                        help="also read transcripts under DIR (a glob is fine); repeat for more")
    args = parser.parse_args()
    if args.days is not None and args.days < 1:
        parser.error("--days must be 1 or more")

    patterns, roots = transcript_roots(args.path)
    files = transcript_files(roots)
    if not files:
        print("No Claude Code transcripts found, so there is nothing to read.")
        print("Looked in:")
        for pattern in patterns:
            print("  " + tilde(pattern))
        print("If yours live somewhere else, run it again with --path DIR.")
        return 1

    cutoff = None
    if args.days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    if len(files) > 200:
        print("Reading %s transcript files..." % format(len(files), ","), file=sys.stderr, flush=True)

    receipt = Receipt()
    seen = set()  # a resumed session repeats earlier responses in its new file
    for path in files:
        for key, (_, model, when, usage) in read_file(path, cutoff).items():
            h = hash(key)
            if h in seen:
                continue
            seen.add(h)
            receipt.add(model, when, usage)

    days = ("%d days" % args.days if args.days != 1 else "day") if args.days else ""
    window = "in the last " + days if args.days else "in these transcripts"
    if receipt.requests == 0:
        print("Read %s transcript files but found no Claude Code requests %s." % (format(len(files), ","), window))
        if receipt.unpriced:
            print("Skipped requests to models this script has no price for: %s" % ", ".join(sorted(receipt.unpriced)))
        if args.days:
            print("Try a longer window, or leave out --days.")
        return 1

    total = receipt.total_cost()
    tokens = receipt.total_tokens()
    title = "Last " + days if args.days else "Everything on this machine"
    print()
    print("Your Claude Code receipt, at Anthropic API list prices")
    span = day(receipt.first) if day(receipt.first) == day(receipt.last) else "%s to %s" % (
        day(receipt.first), day(receipt.last))
    print("%s: %s" % (title, span))
    print()
    print("  Total       %s" % money(total))
    print("  Tokens      %s" % short_tokens(tokens))
    print("  Requests    %s" % format(receipt.requests, ","))
    print()
    print("  %-16s %8s %7s %14s %7s" % ("", "tokens", "share", "cost", "share"))
    for kind, label in TYPES:
        print("  %-16s %8s %6.1f%% %14s %6.1f%%" % (
            label, short_tokens(receipt.tokens[kind]), pct(receipt.tokens[kind], tokens),
            money(receipt.cost[kind]), pct(receipt.cost[kind], total)))
    if receipt.searches:
        web = receipt.searches * WEB_SEARCH
        print("  %-16s %8s %7s %14s %6.1f%%" % ("Web searches", short_tokens(receipt.searches), "",
                                              money(web), pct(web, total)))
    print()
    print("%s of your tokens were the model re-reading cached context." %
          plain_pct(pct(receipt.tokens["cache_read"], tokens)))
    print()
    ranked = sorted(receipt.by_model.items(), key=lambda kv: -kv[1])
    print("By model: " + ", ".join("%s %s (%.0f%%)" % (k.replace("claude-", ""), money(v), pct(v, total))
                                  for k, v in ranked[:4] if v > 0))
    if receipt.unpriced:
        print("Not counted (no price for these models): " + ", ".join(
            "%s (%s request%s)" % (k, format(v, ","), "" if v == 1 else "s")
            for k, v in sorted(receipt.unpriced.items())))
    print("Prices from %s, checked %s." % (PRICES_SOURCE, PRICES_CHECKED))
    print("Read %s transcript files on this machine. Sent nothing anywhere." % format(len(files), ","))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
