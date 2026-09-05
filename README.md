# llm-cost-diff

A lockfile for your LLM API bill. Declare your monthly workloads, commit a
lock, and fail CI when a provider repricing (or a model swap in a PR) moves
your estimated bill beyond a threshold — the drift becomes a visible,
blocking diff instead of a surprise on the invoice.

Prices come from the open dataset at
[apipriceindex.com](https://apipriceindex.com/use-this-data/) (CC BY 4.0,
cross-checked against official pricing pages).

## Quick start

1. Drop `llm-costs.json` at the root of your repo (see
   `llm-costs.example.json`):

```json
{
  "threshold_pct": 10,
  "workloads": [
    { "model": "openai:gpt-5.6-luna", "input_mtok": 200, "output_mtok": 15, "cache_pct": 60 }
  ]
}
```

Model ids are the `id` field of the dataset — browse them at
[apipriceindex.com/use-this-data](https://apipriceindex.com/use-this-data/).

2. Generate and commit the lock:

```
python3 cost_diff.py update
git add llm-costs.lock.json
```

3. Add the check to CI (GitHub Action):

```yaml
- uses: apipriceindex/llm-cost-diff@v0
```

Or run the script directly — it is stdlib-only Python, no dependencies:

```
python3 cost_diff.py check
```

## Behavior

- `check` recomputes the bill against today's index and compares the total
  to the lock. Exit 1 if `|Δ| > threshold_pct` (default 10%).
- `update` (re)writes the lock — run it to accept a drift after review.
- No lock present → warning, exit 0 (first run is never blocking).
- **Index unreachable → warning, exit 0 (fail-open).** An outage of the
  data source must never break *your* CI. Set `LLM_COST_STRICT=1` to make
  it a hard failure instead.
- **Stale index** (`as_of` older than 14 days) → the check still runs, with
  a warning pointing at the index's own
  [health endpoint](https://apipriceindex.com/health.json).
- Cache discount is only applied where the provider publishes a
  cached-input price; no assumed discount otherwise.

## Config reference

| Field | Meaning |
|---|---|
| `threshold_pct` | Max allowed drift of the total monthly bill, in % (default 10) |
| `workloads[].model` | Dataset model id, e.g. `anthropic:sonnet-5` |
| `workloads[].input_mtok` | Input volume, millions of tokens per month |
| `workloads[].output_mtok` | Output volume, millions of tokens per month |
| `workloads[].cache_pct` | Share of input served from cache, 0–100 (default 0) |

Environment overrides: `LLM_COST_CONFIG`, `LLM_COST_LOCK`,
`LLM_COST_INDEX_URL`, `LLM_COST_STRICT`.

## Badge

Live price badge for any tracked endpoint, via the index's shields.io
endpoints:

```markdown
![](https://img.shields.io/endpoint?url=https%3A%2F%2Fapipriceindex.com%2Fapi%2Fbadge%2Fopenai%2Fgpt-5.6-luna.json)
```

## License

Code: MIT. Price data: [CC BY 4.0](https://apipriceindex.com/use-this-data/),
attribution "API Price Index".
