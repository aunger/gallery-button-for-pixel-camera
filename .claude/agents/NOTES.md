# Claude effort levels

Reference notes on the `effort` setting for the current Claude models, plus rough
estimates of relative token spend for Opus 4.8.

Effort modulates how much the model reasons (thinking-token spend) and, secondarily, how
persistently it works and how long its output runs.
On the Claude API it is the `output_config.effort` parameter; in Claude Code it is the
session effort level (set via `/effort`, `--effort`, `CLAUDE_CODE_EFFORT_LEVEL`, or
`effortLevel` in settings).

## Available and default effort by model

| Model | Model ID | Available effort levels | Default (API) | Default (Claude Code) |
| --- | --- | --- | --- | --- |
| Opus 4.8 | `claude-opus-4-8` | low, medium, high, xhigh, max | high | high |
| Sonnet 4.6 | `claude-sonnet-4-6` | low, medium, high, max | high | high |
| Haiku 4.5 | `claude-haiku-4-5` | not supported | n/a | n/a |

Notes:
- `xhigh` is available on Opus 4.8 (and Opus 4.7) but not on Sonnet 4.6.
- Haiku 4.5 does not support the `effort` parameter at all, and does not support
  adaptive thinking. It does support manual extended thinking.
- For Sonnet 4.6 the API default is `high`, but Anthropic's guidance suggests starting
  at `medium` for most applications to avoid unexpected latency.
- Opus 4.8 uses adaptive thinking as its only thinking mode (manual `budget_tokens` is
  rejected); `effort` is the recommended way to control reasoning depth.

## Estimated token-spend ratios for Opus 4.8

These are estimates, not documented figures.
Anthropic does not publish per-level token-spend ratios; effort is described
qualitatively.
Treat the numbers below as order-of-magnitude only, with `high` normalized
to 1.0.

| Effort | Estimated relative total token spend | Notes |
| --- | --- | --- |
| low | ~0.2-0.4x | Often skips thinking entirely on easy problems, so can run much lower. |
| medium | ~0.5-0.7x | Thinks, but briefly. |
| high | 1.0x (baseline) | Almost always thinks. |
| xhigh | ~1.5-2.5x | Noticeably deeper reasoning. |
| max | ~3-6x+ | Thinking caps lifted; highly variable and can exceed this on hard problems. |

Caveats:
- The spread is widest on thinking-heavy tasks, where effort has the most leverage.
  On output-dominated tasks (producing a long file with little reasoning) all levels
  compress much closer to 1.0x, because the thinking delta is a small fraction of the
  total.
- `max` is the least predictable: it removes thinking caps, so its ratio tracks problem
  difficulty more than any fixed multiplier.
  A simple task at `max` may be near 1x (nothing to think about); a genuinely hard one
  can be many times `high`.
- Latency and cost roughly track these ratios, since thinking tokens are billed.
- For accurate figures on a specific workload, run one representative task at each level
  and read the `usage` (input/output/thinking tokens) rather than relying on these
  estimates.

## Sources

Model effort support and defaults are from the Claude API effort documentation, the
Claude Code model configuration documentation, and the Claude models overview (as of
June 2026; effort support is model- and version-dependent).
The Opus 4.8 spend ratios are author estimates, not from Anthropic documentation.
