# Scoring Path Audit

## Scope

- File: `밴픽 최종 v2_merged.html`
- Audit target: `scoreHero(...)`
- Date: `2026-03-18`
- Policy: no score formula changes, no helper formula changes, no UI changes

## Overlay State And Fallback

- Runtime overlay primary state: `runtimeOverlayState.ready && runtimeOverlayState.data?.heroes`
- Overlay loaded path:
  - `helpsWith/goodVs/badVs`: runtime overlay v2 row
  - `firstpick/vanguard/preban/banPressure/presence`: runtime overlay role entries
  - `protection`: runtime overlay protection entries
- Overlay fallback path:
  - if overlay is not loaded, overlay helpers return `0` or empty arrays
  - `meta`, `exposure`, `relief`, `archetype`, `reproducibility` still work from baseline/board logic

## Bucket Audit

| Bucket | Active source function | Active source data | Old path direct scoring in total |
| --- | --- | --- | --- |
| `meta` | `getBaselineMetaScore` | baseline hero `pick/win/ban` + `heroReliabilityMultiplier` | `false` |
| `synergy` | `getOverlaySynergyBonus` | overlay `helpsWith` + `getOverlaySynergySummary(myPicks)` | `false` |
| `counters` | `getOverlayCounterBonus` | overlay `goodVs` minus overlay `badVs` on current opp board | `false` |
| `completion` | `getOverlayCompletionBonus` | overlay `helpsWith` coverage + current my board | `false` |
| `early` | `getOverlayRoleBonus` | overlay `firstpick` + `presence` + `protection.earlyStageGate` | `false` |
| `urgency` | `getOverlayRoleBonus` | overlay `preban` + `banPressure` | `false` |
| `vanguard` | `getOverlayRoleBonus` | overlay `vanguard` + `protection.lateStageRelief` | `false` |
| `exposure` | `finalBanExposurePenalty` | baseline hero ban exposure + my board final-ban profile | `false` |
| `relief` | `decoyRelief + applyAuxiliaryBonusCap` | baseline decoy relief + auxiliary cap | `false` |
| `archetype` | `turnOneArchetypeBonus` | hero tags + current board + stage | `false` |
| `reproducibility` | `turnOneReplicabilityPenalty` | hero tags + Harseti preban/field state + board | `false` |
| `openCounter` | `getOverlayOpenCounterPenalty` | overlay `badVs` + current opp board + projected overlay threats + Harseti state | `false` |

## Old Path Direct Scoring Flags

- `PACKAGES direct scoring`: `false`
- `NUANCED_COUNTERS direct scoring`: `false`
- `legacy compiled pattern direct scoring`: `false`
- `battlecollect direct scoring`: `false`
- `speedContest direct scoring`: `false`
- `firstTurnPenalty direct scoring`: `false`
- `logTurnBonus direct scoring`: `false`
- `repeatAxisPenalty direct scoring`: `false`

## Total Formula Audit

Current total is:

```text
meta + synergy + counters + completion + early + urgency + exposure + relief + vanguard + archetype + reproducibility + openCounter
```

The following legacy buckets are not directly added to total:

- `pack`
- `patternParts.*`
- `relationBucket.*`
- `packageBonus(...)`
- `counterBonus(...)`
- `completionPressureBonus(...)`
- `getBattlecollectAdjustment(...)`
- `projectedOpenCounterPenalty(...)`
- `speedContestSkirmisherBonus(...)`
- `vanguardFirstTurnPenalty(...)`
- `turnScopedLogWinBonus(...)`

## Validation Heroes

Validation set:

- `SCENT_POLITIS`
- `GIO`
- `MAID_CHLOE`
- `SEOLHWA`
- `FRIEREN`

### Shared bucket source dump

All five heroes currently use the same bucket routing inside `scoreHero(...)`:

- `metaSource = baseline`
- `synergySource = overlay`
- `counterSource = overlay`
- `completionSource = overlay+board`
- `earlySource = overlayRoles`
- `vanguardSource = overlayRoles`
- `urgencySource = overlayRoles`
- `exposureSource = baselineBan`
- `reliefSource = baselineDecoy`
- `archetypeSource = tags+board`
- `reproducibilitySource = tags+harseti+board`
- `openCounterSource = overlay+board`

### Overlay row status

#### `SCENT_POLITIS`

- `helpsWith = 8`
- `goodVs = 8`
- `badVs = 8`
- `fallbackUsed = false`
- `helpsWith sources = legendWith:3, pairLift:5`
- `goodVs sources = legendHard:6, observedGoodVs:2`
- `badVs sources = legendHard:3, observedBadVs:5`

#### `GIO`

- `helpsWith = 8`
- `goodVs = 8`
- `badVs = 8`
- `fallbackUsed = false`
- `helpsWith sources = legendWith:3, pairLift:5`
- `goodVs sources = legendHard:8`
- `badVs sources = legendHard:3, observedBadVs:5`

#### `MAID_CHLOE`

- `helpsWith = 8`
- `goodVs = 8`
- `badVs = 8`
- `fallbackUsed = false`
- `helpsWith sources = legendWith:3, pairLift:5`
- `goodVs sources = legendHard:3, observedGoodVs:5`
- `badVs sources = legendHard:3, observedBadVs:5`

#### `SEOLHWA`

- `helpsWith = 8`
- `goodVs = 8`
- `badVs = 8`
- `fallbackUsed = false`
- `helpsWith sources = legendWith:3, pairLift:5`
- `goodVs sources = legendHard:4, observedGoodVs:4`
- `badVs sources = legendHard:3, observedBadVs:5`

#### `FRIEREN`

- `helpsWith = 8`
- `goodVs = 8`
- `badVs = 8`
- `fallbackUsed = false`
- `helpsWith sources = legendWith:3, pairLift:5`
- `goodVs sources = legendHard:8`
- `badVs sources = legendHard:3, observedBadVs:5`

## Notes

- Debug now exposes the per-bucket source labels and a nested `scorePathAudit` object.
- This audit is structural only. It does not claim that the chosen data is strategically optimal; it only documents which path is active in total today.
