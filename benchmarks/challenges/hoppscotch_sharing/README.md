# hoppscotch_sharing

Paths in `ideal_context.json` are relative to
`<hoppscotch-repo>/packages/hoppscotch-backend` (monorepo subdir, not
the repo root). Always pass that subdir as `--source-dir` when running
the benchmark:

```bash
.venv/Scripts/python -m benchmarks.plan_factory decomposed \
  --runs 5 \
  --source-dir ../hoppscotch/packages/hoppscotch-backend \
  --context-file benchmarks/challenges/hoppscotch_sharing/ideal_context.json \
  --query "$(cat benchmarks/challenges/hoppscotch_sharing/user_prompt.txt)" \
  --taxonomy benchmarks/challenges/hoppscotch_sharing/taxonomy.json \
  --score-v2
```

Running against `../hoppscotch` (repo root) causes
`AgentContextGatherer` to silently drop every override file — none of
the paths resolve — and the model either improvises (best case, ~40%)
or refuses the decision_decomposition step (failures, ~60%).
