Do a compile/lint pass on the research knowledge base. This is a Karpathy-style "health check" — scan for stale info, missing articles, contradictions, and update.

## Steps

1. **Read all research/ files** — INDEX.md, every article in experiments/, related-work/, decisions/, paper/
2. **Read session_log.md** — Check if any recent decisions haven't been compiled into research/ yet
3. **Read recent git log** (`git log --oneline -20`) — Check if any commits describe decisions or results not reflected in research/
4. **Check Linear issues** — Use `mcp__linear__list_issues` for team KAE. Check if any issue status changes or new issues should update research/
5. **For each research/ file, check:**
   - Are the facts still accurate? (e.g., dataset counts, model versions, run status)
   - Are there claims without sources? Flag them.
   - Are there stale references? (e.g., "pending" items that are now done)
   - Are there missing cross-references between articles?
6. **Check INDEX.md gaps section** — Are any gaps now fillable based on new data/decisions?
7. **Report findings** — List what's stale, what's missing, what needs updating
8. **Make updates** — Fix stale facts, add missing information, create new articles if warranted
9. **Update INDEX.md** — Add any new articles, remove filled gaps

## Rules
- Only update with information from authoritative sources (code, git, Linear, session_log)
- Do not hallucinate results or metrics — if you don't have the data, add it to the gaps list
- Mark anything uncertain with "UNVERIFIED:" prefix
- Keep articles focused — conclusions and decisions, not stream-of-consciousness
- Reference KAE issue numbers inline where relevant
