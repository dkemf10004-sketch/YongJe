from __future__ import annotations

import json
import math
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / '밴픽 시뮬'
COMPILED_HEROES_PATH = DATA_DIR / 'compiled_heroes.json'
LEGEND_PATH = DATA_DIR / 'epic7_hero_record_output' / 'hero_full_legend.json'
PATTERNS_PATH = DATA_DIR / 'compiled_patterns.json'
MATCHUP_OUT = DATA_DIR / 'compiled_matchup_matrix.json'
SYNERGY_OUT = DATA_DIR / 'compiled_synergy_matrix.json'
ROLE_OUT = DATA_DIR / 'compiled_role_scores.json'
OVERLAY_OUT = DATA_DIR / 'compiled_runtime_overlay.json'


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def sample_confidence(sample: float, pivot: float) -> float:
    if sample <= 0:
        return 0.0
    return clamp(math.log1p(sample) / math.log1p(pivot), 0.0, 1.0)


def round4(value: float) -> float:
    return round(float(value), 4)


def normalize_lookup_name(raw: str) -> str:
    return ''.join(str(raw or '').replace('_', ' ').split()).lower()


def resolve_overlay_relation_id(raw_id: str, hero_by_id: dict, alias_map: dict, name_to_id: dict, normalized_name_to_id: dict) -> tuple[str | None, bool, bool]:
    if raw_id in hero_by_id:
        return raw_id, False, False
    alias_target = alias_map.get(raw_id)
    if alias_target and alias_target in hero_by_id:
        return alias_target, True, False
    human_name = raw_id[4:].replace('_', ' ').strip() if raw_id.startswith('EXT_') else str(raw_id or '').strip()
    if human_name in name_to_id:
        return name_to_id[human_name], True, False
    normalized_name = normalize_lookup_name(human_name)
    if normalized_name in normalized_name_to_id:
        return normalized_name_to_id[normalized_name], True, False
    return raw_id, False, True


SOURCE_PRIORITY = [
    'legendWith',
    'legendHard',
    'pairLift',
    'packageLift',
    'weakHint',
    'reverseWeakHint',
    'banPressure',
]


def pick_relation_source(entry: dict) -> str:
    sources = entry.get('sources') or {}
    for key in SOURCE_PRIORITY:
        if sources.get(key):
            return key
    if sources:
        return sorted(sources.keys())[0]
    return 'matrix'


def count_relation_sources(relations: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for item in relations:
        source = str(item.get('source') or 'unknown')
        counts[source] = counts.get(source, 0) + 1
    return counts


RELATION_LIMIT = 8
MIN_RELATION_COUNT = 8


def relation_sort_tuple(item: dict) -> tuple:
    return (
        float(item.get('_priority', 0.0)),
        float(item.get('_sort_score', item.get('score', 0.0))),
        float(item.get('confidence', 0.0)),
    )


def merge_relation_item(bucket: dict[str, dict], item: dict) -> None:
    prev = bucket.get(item['id'])
    if prev is None or relation_sort_tuple(item) > relation_sort_tuple(prev):
        bucket[item['id']] = item


def finalize_relation_items(items: list[dict], limit: int = RELATION_LIMIT) -> list[dict]:
    ordered = sorted(
        items,
        key=lambda item: (-float(item.get('_priority', 0.0)), -float(item.get('_sort_score', item.get('score', 0.0))), -float(item.get('confidence', 0.0)), item['id']),
    )[:limit]
    cleaned: list[dict] = []
    for item in ordered:
        cleaned.append({
            'id': item['id'],
            'score': round4(float(item.get('score', 0.0))),
            'confidence': round4(float(item.get('confidence', 0.0))),
            'source': str(item.get('source') or 'unknown'),
        })
    return cleaned


def resolve_relation_candidate(
    target_id: str,
    entry: dict,
    hero_id: str,
    hero_by_id: dict,
    alias_map: dict,
    name_to_id: dict,
    normalized_name_to_id: dict,
    *,
    include_zero: bool = False,
    source_override: str | None = None,
    priority: float = 3.0,
    damp: float = 1.0,
    floor_score: float = 0.0,
    floor_conf: float = 0.08,
) -> tuple[dict | None, str | None, bool]:
    resolved_id, used_fallback, unresolved_flag = resolve_overlay_relation_id(
        target_id,
        hero_by_id,
        alias_map,
        name_to_id,
        normalized_name_to_id,
    )
    if not resolved_id or resolved_id == hero_id:
        return None, None, used_fallback
    raw_score = float(entry.get('rawScore') or 0.0)
    score = float(entry.get('score') or 0.0)
    confidence = float(entry.get('confidence') or 0.0)
    if max(raw_score, score) <= 0 and not include_zero:
        return None, None, used_fallback
    adjusted_confidence = max(floor_conf, confidence if confidence > 0 else floor_conf)
    adjusted_score = max(score, raw_score * max(0.12, adjusted_confidence * 0.72), floor_score) * damp
    if source_override == 'fallback':
        adjusted_score = max(floor_score, min(0.18, adjusted_score if adjusted_score > 0 else floor_score))
        adjusted_confidence = max(floor_conf, min(0.24, adjusted_confidence))
    if adjusted_score <= 0:
        return None, None, used_fallback
    item = {
        'id': resolved_id,
        'score': round4(adjusted_score),
        'confidence': round4(adjusted_confidence),
        'source': source_override or pick_relation_source(entry),
        '_priority': priority,
        '_sort_score': max(score, raw_score, adjusted_score),
    }
    return item, (resolved_id if unresolved_flag else None), used_fallback


def build_relation_candidates_from_row(
    row: dict,
    hero_id: str,
    hero_by_id: dict,
    alias_map: dict,
    name_to_id: dict,
    normalized_name_to_id: dict,
    *,
    include_zero: bool = False,
    source_override: str | None = None,
    priority: float = 3.0,
    damp: float = 1.0,
    floor_score: float = 0.0,
    floor_conf: float = 0.08,
) -> tuple[list[dict], dict[str, dict], list[str], bool]:
    items: list[dict] = []
    row_lookup: dict[str, dict] = {}
    unresolved_ids: list[str] = []
    fallback_used = False
    for target_id, entry in (row or {}).items():
        item, unresolved_id, used_fallback = resolve_relation_candidate(
            target_id,
            entry,
            hero_id,
            hero_by_id,
            alias_map,
            name_to_id,
            normalized_name_to_id,
            include_zero=include_zero,
            source_override=source_override,
            priority=priority,
            damp=damp,
            floor_score=floor_score,
            floor_conf=floor_conf,
        )
        fallback_used = fallback_used or used_fallback
        if unresolved_id:
            unresolved_ids.append(unresolved_id)
        if not item:
            continue
        row_lookup[item['id']] = entry
        items.append(item)
    return items, row_lookup, unresolved_ids, fallback_used


def build_reverse_row(counter_rows: dict, hero_id: str) -> dict:
    reverse_row: dict[str, dict] = {}
    for other_id, row in (counter_rows or {}).items():
        if other_id == hero_id:
            continue
        entry = (row or {}).get(hero_id)
        if entry:
            reverse_row[other_id] = entry
    return reverse_row


def dedupe_relation_ids(ids: list[str], hero_id: str, hero_by_id: dict) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for target_id in ids:
        if not target_id or target_id == hero_id or target_id in seen or target_id not in hero_by_id:
            continue
        seen.add(target_id)
        result.append(target_id)
    return result


def build_baseline_relation_ids(relation_key: str, hero: dict, heroes: list[dict]) -> list[str]:
    hero_id = hero['id']
    if relation_key == 'helpsWith':
        ids = list(hero.get('syn') or [])
        ids.extend(other['id'] for other in heroes if hero_id in set(other.get('syn') or []))
    elif relation_key == 'goodVs':
        ids = [other['id'] for other in heroes if hero_id in set(other.get('hard') or [])]
    else:
        ids = list(hero.get('hard') or [])
    return dedupe_relation_ids(ids, hero_id, {item['id']: item for item in heroes})


def baseline_source_name(relation_key: str) -> str:
    if relation_key == 'helpsWith':
        return 'baselineSyn'
    if relation_key == 'goodVs':
        return 'baselineHardReverse'
    return 'baselineHard'


def make_baseline_relation_item(relation_key: str, target_id: str, row_lookup: dict[str, dict]) -> dict:
    base_values = {
        'helpsWith': (0.22, 0.28),
        'goodVs': (0.24, 0.30),
        'badVs': (0.24, 0.30),
    }
    base_score, base_confidence = base_values[relation_key]
    entry = (row_lookup or {}).get(target_id)
    if entry and max(float(entry.get('rawScore') or 0.0), float(entry.get('score') or 0.0)) > 0:
        row_confidence = float(entry.get('confidence') or 0.0)
        row_score = float(entry.get('score') or 0.0)
        row_raw = float(entry.get('rawScore') or 0.0)
        return {
            'id': target_id,
            'score': round4(max(base_score, max(row_score, row_raw * max(0.12, max(row_confidence, 0.08) * 0.72)) * 0.84)),
            'confidence': round4(max(base_confidence, min(0.38, max(row_confidence, 0.08) * 0.86))),
            'source': baseline_source_name(relation_key),
            '_priority': 2.2,
            '_sort_score': max(row_score, row_raw, base_score),
        }
    return {
        'id': target_id,
        'score': round4(base_score),
        'confidence': round4(base_confidence),
        'source': baseline_source_name(relation_key),
        '_priority': 2.0,
        '_sort_score': base_score,
    }


def make_generic_fallback_item(target_id: str, hero_by_id: dict) -> dict:
    hero = hero_by_id[target_id]
    pick = float(hero.get('pick') or 0.0)
    return {
        'id': target_id,
        'score': round4(0.05 + min(0.04, pick * 0.0015)),
        'confidence': 0.08,
        'source': 'fallback',
        '_priority': 0.4,
        '_sort_score': 0.05 + pick * 0.0015,
    }


def backfill_relation_list(
    relation_key: str,
    hero: dict,
    heroes: list[dict],
    hero_by_id: dict,
    primary_candidates: list[dict],
    row_lookup: dict[str, dict],
    all_ids: list[str],
) -> tuple[list[dict], dict]:
    chosen: dict[str, dict] = {}
    for item in primary_candidates:
        merge_relation_item(chosen, item)
    before_count = len(chosen)

    for target_id in build_baseline_relation_ids(relation_key, hero, heroes):
        if len(chosen) >= MIN_RELATION_COUNT:
            break
        if target_id in chosen:
            continue
        merge_relation_item(chosen, make_baseline_relation_item(relation_key, target_id, row_lookup))

    if len(chosen) < MIN_RELATION_COUNT:
        weak_candidates, _, _, _ = build_relation_candidates_from_row(
            row_lookup,
            hero['id'],
            hero_by_id,
            {},
            {},
            {},
        )
        del weak_candidates

    remaining_from_row = []
    for target_id, entry in (row_lookup or {}).items():
        if target_id in chosen:
            continue
        fallback_item = {
            'id': target_id,
            'score': round4(max(0.06, min(0.18, max(float(entry.get('score') or 0.0), float(entry.get('rawScore') or 0.0)) * 0.72))),
            'confidence': round4(max(0.10, min(0.24, max(float(entry.get('confidence') or 0.0), 0.10)))),
            'source': 'fallback',
            '_priority': 1.0,
            '_sort_score': max(float(entry.get('score') or 0.0), float(entry.get('rawScore') or 0.0), 0.06),
        }
        remaining_from_row.append(fallback_item)
    remaining_from_row.sort(key=lambda item: (-item['_sort_score'], -item['confidence'], item['id']))
    for item in remaining_from_row:
        if len(chosen) >= MIN_RELATION_COUNT:
            break
        if item['id'] in chosen:
            continue
        merge_relation_item(chosen, item)

    for target_id in all_ids:
        if len(chosen) >= MIN_RELATION_COUNT:
            break
        if target_id == hero['id'] or target_id in chosen:
            continue
        merge_relation_item(chosen, make_generic_fallback_item(target_id, hero_by_id))

    final_items = finalize_relation_items(list(chosen.values()))
    diagnostics = {
        'availableBeforeBackfill': before_count,
        'availableAfterBackfill': len(final_items),
        'shortageBefore': before_count < MIN_RELATION_COUNT,
        'shortageAfter': len(final_items) < MIN_RELATION_COUNT,
        'fallbackInserted': sum(1 for item in final_items if item.get('source') == 'fallback'),
    }
    return final_items, diagnostics


def build_role_overlay_value(section: dict, hero_id: str) -> dict:
    entry = (section or {}).get(hero_id, {}) or {}
    return {
        'score': round4(float(entry.get('score') or 0.0)),
        'confidence': round4(float(entry.get('confidence') or 0.0)),
    }


def build_runtime_overlay(compiled_heroes: dict, counter_matrix: dict, synergy_matrix: dict, role_scores: dict) -> dict:
    heroes = compiled_heroes['heroes']
    hero_by_id = {hero['id']: hero for hero in heroes}
    alias_map = compiled_heroes.get('aliases', {}) or {}
    name_to_id = {hero['name']: hero['id'] for hero in heroes}
    normalized_name_to_id = {normalize_lookup_name(hero['name']): hero['id'] for hero in heroes}
    counter_rows = counter_matrix.get('counterMatrix', {}) or {}
    synergy_rows = synergy_matrix.get('synergyMatrix', {}) or {}
    overlay_heroes = {}
    unresolved_ids: list[str] = []
    all_ids = [hero['id'] for hero in heroes]
    total_helps = 0
    total_good = 0
    total_bad = 0
    min_helps = RELATION_LIMIT
    min_good = RELATION_LIMIT
    min_bad = RELATION_LIMIT
    heroes_below_before = 0
    heroes_below_after = 0
    fallback_inserted_count = 0

    for hero in heroes:
        hero_id = hero['id']
        helps_primary, helps_lookup, helps_unresolved, helps_fallback_used = build_relation_candidates_from_row(
            synergy_rows.get(hero_id, {}),
            hero_id,
            hero_by_id,
            alias_map,
            name_to_id,
            normalized_name_to_id,
        )
        good_primary, good_lookup, good_unresolved, good_fallback_used = build_relation_candidates_from_row(
            counter_rows.get(hero_id, {}),
            hero_id,
            hero_by_id,
            alias_map,
            name_to_id,
            normalized_name_to_id,
        )
        reverse_counter_row = build_reverse_row(counter_rows, hero_id)
        bad_primary, bad_lookup, bad_unresolved, bad_fallback_used = build_relation_candidates_from_row(
            reverse_counter_row,
            hero_id,
            hero_by_id,
            alias_map,
            name_to_id,
            normalized_name_to_id,
        )

        helps_with, helps_diag = backfill_relation_list('helpsWith', hero, heroes, hero_by_id, helps_primary, helps_lookup, all_ids)
        good_vs, good_diag = backfill_relation_list('goodVs', hero, heroes, hero_by_id, good_primary, good_lookup, all_ids)
        bad_vs, bad_diag = backfill_relation_list('badVs', hero, heroes, hero_by_id, bad_primary, bad_lookup, all_ids)

        hero_unresolved = [
            *[f'{hero_id}:helpsWith:{raw}' for raw in helps_unresolved],
            *[f'{hero_id}:goodVs:{raw}' for raw in good_unresolved],
            *[f'{hero_id}:badVs:{raw}' for raw in bad_unresolved],
        ]
        unresolved_ids.extend(hero_unresolved)

        total_helps += len(helps_with)
        total_good += len(good_vs)
        total_bad += len(bad_vs)
        min_helps = min(min_helps, len(helps_with))
        min_good = min(min_good, len(good_vs))
        min_bad = min(min_bad, len(bad_vs))
        fallback_inserted_count += helps_diag['fallbackInserted'] + good_diag['fallbackInserted'] + bad_diag['fallbackInserted']
        if helps_diag['shortageBefore'] or good_diag['shortageBefore'] or bad_diag['shortageBefore']:
            heroes_below_before += 1
        if helps_diag['shortageAfter'] or good_diag['shortageAfter'] or bad_diag['shortageAfter']:
            heroes_below_after += 1

        presence_entry = (role_scores.get('presence', {}) or {}).get(hero_id, {}) or {}
        protection_entry = (role_scores.get('protection', {}) or {}).get(hero_id, {}) or {}
        overlay_heroes[hero_id] = {
            'helpsWith': helps_with,
            'goodVs': good_vs,
            'badVs': bad_vs,
            'firstpick': build_role_overlay_value(role_scores.get('firstpick', {}), hero_id),
            'vanguard': build_role_overlay_value(role_scores.get('vanguard', {}), hero_id),
            'preban': build_role_overlay_value(role_scores.get('preban', {}), hero_id),
            'banPressure': build_role_overlay_value(role_scores.get('banPressure', {}), hero_id),
            'protection': {
                'relationCapScale': round4(float(protection_entry.get('relationCapScale') or 0.0)),
                'confidenceCapScale': round4(float(protection_entry.get('confidenceCapScale') or 0.0)),
                'earlyStageGate': round4(float(protection_entry.get('earlyStageGate') or 0.0)),
                'lateStageRelief': round4(float(protection_entry.get('lateStageRelief') or 0.0)),
            },
            'diagnostics': {
                'presenceRate': round4(float(presence_entry.get('rate') or presence_entry.get('score') or 0.0)),
                'sourceCounts': {
                    'helpsWith': count_relation_sources(helps_with),
                    'goodVs': count_relation_sources(good_vs),
                    'badVs': count_relation_sources(bad_vs),
                    'relationCounts': {
                        'helpsWith': len(helps_with),
                        'goodVs': len(good_vs),
                        'badVs': len(bad_vs),
                    },
                    'shortages': {
                        'helpsWith': bool(helps_diag['shortageAfter']),
                        'goodVs': bool(good_diag['shortageAfter']),
                        'badVs': bool(bad_diag['shortageAfter']),
                    },
                    'beforeBackfill': {
                        'helpsWith': int(helps_diag['availableBeforeBackfill']),
                        'goodVs': int(good_diag['availableBeforeBackfill']),
                        'badVs': int(bad_diag['availableBeforeBackfill']),
                    },
                },
                'fallbackUsed': bool(
                    helps_fallback_used
                    or good_fallback_used
                    or bad_fallback_used
                    or helps_diag['fallbackInserted']
                    or good_diag['fallbackInserted']
                    or bad_diag['fallbackInserted']
                ),
            },
        }

    return {
        'heroes': overlay_heroes,
        'meta': {
            'heroCount': len(heroes),
            'sourceVersion': f"overlay-v2/matrix-{counter_matrix.get('version', 1)}-{synergy_matrix.get('version', 1)}-role-{role_scores.get('version', 1)}",
            'buildSummary': {
                'relationLimit': RELATION_LIMIT,
                'minHelpsWithCount': int(min_helps if heroes else 0),
                'minGoodVsCount': int(min_good if heroes else 0),
                'minBadVsCount': int(min_bad if heroes else 0),
                'avgHelpsWith': round4(total_helps / max(1, len(heroes))),
                'avgGoodVs': round4(total_good / max(1, len(heroes))),
                'avgBadVs': round4(total_bad / max(1, len(heroes))),
                'heroesBelowThresholdBeforeBackfill': int(heroes_below_before),
                'heroesBelowThresholdAfterBackfill': int(heroes_below_after),
                'fallbackInsertedCount': int(fallback_inserted_count),
                'unresolvedIds': unresolved_ids,
                'unresolvedCount': len(unresolved_ids),
                'shortageHeroes': heroes_below_after,
                'sourceHeroCount': len(heroes),
                'counterNonzero': int(counter_matrix.get('buildSummary', {}).get('nonzeroRelations', 0)),
                'synergyNonzero': int(synergy_matrix.get('buildSummary', {}).get('nonzeroRelations', 0)),
                'lowPickSuppressedHeroes': int(role_scores.get('buildSummary', {}).get('lowPickSuppressedHeroes', 0)),
            }
        }
    }

def zero_relation() -> dict:
    return {
        'score': 0.0,
        'rawScore': 0.0,
        'confidence': 0.0,
        'sample': 0,
        'cap': 0.0,
        'sources': {},
    }


def build_role_entry(rate: float, raw_score: float, sample: int, cap: float, pivot: float, presence_gate: float) -> dict:
    confidence = sample_confidence(sample, pivot) * (0.34 + presence_gate * 0.66)
    score = clamp(raw_score, 0.0, cap) * confidence
    return {
        'rate': round4(rate),
        'rawScore': round4(raw_score),
        'score': round4(score),
        'confidence': round4(confidence),
        'sample': int(sample),
        'cap': round4(cap),
    }


def main() -> None:
    compiled_heroes = read_json(COMPILED_HEROES_PATH)
    legend_rows = read_json(LEGEND_PATH)
    patterns = read_json(PATTERNS_PATH)

    heroes = compiled_heroes['heroes']
    hero_count = len(heroes)
    name_to_id = {hero['name']: hero['id'] for hero in heroes}
    id_to_name = {hero['id']: hero['name'] for hero in heroes}
    hero_by_id = {hero['id']: hero for hero in heroes}
    legend_by_name = {row['hero_name']: row for row in legend_rows if row.get('hero_name') in name_to_id}

    presence_stats = patterns.get('heroPresenceStats', {})
    firstpick_stats = patterns.get('heroFirstPickStats', {})
    vanguard_stats = patterns.get('heroVanguardStats', {})
    preban_stats = patterns.get('heroPrebanStats', {})
    ban_pressure_stats = patterns.get('heroBanPressureStats', {})
    pair_stats = patterns.get('heroPairStats', {})
    package_stats = patterns.get('heroPackageStats', {})
    weak_hints = patterns.get('weakMatchupHintStats', {})
    total_teams = int(patterns.get('source', {}).get('totalTeams') or 0)

    pair_package_support: dict[tuple[str, str], list[dict]] = {}
    for key, stat in package_stats.items():
        names = [part.strip() for part in str(key).split('|') if part.strip() in name_to_id]
        ids = sorted({name_to_id[name] for name in names})
        if len(ids) < 2:
            continue
        lift = max(0.0, float(stat.get('lift') or 0.0))
        games = int(stat.get('games') or 0)
        if lift <= 0 or games <= 0:
            continue
        support = clamp(lift * 0.34, 0.0, 0.28)
        weighted = support * (0.30 + sample_confidence(games, 84) * 0.70)
        for a, b in combinations(ids, 2):
            pair_package_support.setdefault((a, b), []).append({
                'games': games,
                'lift': round4(lift),
                'support': round4(weighted),
            })

    role_scores = {
        'version': 1,
        'source': {
            'legend': str(LEGEND_PATH.relative_to(ROOT)).replace('\\', '/'),
            'compiled_patterns': str(PATTERNS_PATH.relative_to(ROOT)).replace('\\', '/'),
        },
        'firstpick': {},
        'vanguard': {},
        'preban': {},
        'banPressure': {},
        'presence': {},
        'protection': {},
        'buildSummary': {},
    }

    low_pick_suppressed = 0
    for hero in heroes:
        hero_id = hero['id']
        name = hero['name']
        presence = presence_stats.get(name, {})
        presence_rate = float(presence.get('presenceRate') or 0.0)
        presence_total = int(presence.get('total') or 0)
        fallback_pick_rate = float(hero.get('pick') or 0.0) / 100.0
        effective_presence = max(presence_rate, fallback_pick_rate * 0.82)
        presence_gate = clamp((effective_presence - 0.018) / 0.085, 0.0, 1.0)
        meta_gate = clamp((float(hero.get('pick') or 0.0) - 4.0) / 16.0, 0.0, 1.0)

        fp = firstpick_stats.get(name, {})
        vg = vanguard_stats.get(name, {})
        pb = preban_stats.get(name, {})
        bp = ban_pressure_stats.get(name, {})

        role_scores['firstpick'][hero_id] = build_role_entry(
            float(fp.get('firstPickRate') or 0.0),
            float(fp.get('openerScore') or 0.0) * 3.2,
            int(fp.get('firstPickCount') or 0),
            1.1,
            48,
            presence_gate,
        )
        role_scores['vanguard'][hero_id] = build_role_entry(
            float(vg.get('vanguardRate') or 0.0),
            float(vg.get('protectedCoreScore') or 0.0) * 3.4,
            int(vg.get('vanguardCount') or 0),
            1.2,
            56,
            presence_gate,
        )
        role_scores['preban'][hero_id] = build_role_entry(
            float(pb.get('prebanRate') or 0.0),
            float(pb.get('pressureScore') or 0.0) * 2.8,
            int(pb.get('prebanCount') or 0),
            0.92,
            64,
            presence_gate,
        )
        role_scores['banPressure'][hero_id] = build_role_entry(
            float(bp.get('banRate') or 0.0),
            float(bp.get('banPressureScore') or 0.0) * 2.6,
            int(bp.get('banCount') or 0),
            0.95,
            48,
            presence_gate,
        )
        role_scores['presence'][hero_id] = {
            'rate': round4(effective_presence),
            'rawScore': round4(effective_presence),
            'score': round4(effective_presence),
            'confidence': round4(sample_confidence(presence_total, max(total_teams, 1))),
            'sample': presence_total,
            'cap': 1.0,
        }
        protection = {
            'presenceRate': round4(effective_presence),
            'relationCapScale': round4(0.82 + presence_gate * 1.36),
            'confidenceCapScale': round4(0.72 + presence_gate * 0.88),
            'earlyStageGate': round4(0.58 + max(presence_gate, meta_gate) * 0.42),
            'lateStageRelief': round4(0.92 + (1.0 - presence_gate) * 0.36),
            'suppressed': bool(effective_presence < 0.024 or (float(hero.get('pick') or 0.0) < 5.0 and effective_presence < 0.032)),
        }
        if protection['suppressed']:
            low_pick_suppressed += 1
        role_scores['protection'][hero_id] = protection

    counter_matrix = {
        'version': 1,
        'source': {
            'legend': str(LEGEND_PATH.relative_to(ROOT)).replace('\\', '/'),
            'compiled_patterns': str(PATTERNS_PATH.relative_to(ROOT)).replace('\\', '/'),
        },
        'heroes': [{'id': hero['id'], 'name': hero['name']} for hero in heroes],
        'counterMatrix': {},
        'buildSummary': {},
    }
    synergy_matrix = {
        'version': 1,
        'source': {
            'legend': str(LEGEND_PATH.relative_to(ROOT)).replace('\\', '/'),
            'compiled_patterns': str(PATTERNS_PATH.relative_to(ROOT)).replace('\\', '/'),
        },
        'heroes': [{'id': hero['id'], 'name': hero['name']} for hero in heroes],
        'synergyMatrix': {},
        'buildSummary': {},
    }

    counter_nonzero = 0
    synergy_nonzero = 0
    confidence_weakened = 0

    for hero in heroes:
        a_id = hero['id']
        a_name = hero['name']
        a_presence = float(role_scores['presence'][a_id]['rate'])
        counter_row = {}
        synergy_row = {}
        for opp in heroes:
            b_id = opp['id']
            b_name = opp['name']
            if a_id == b_id:
                counter_row[b_id] = zero_relation()
                synergy_row[b_id] = zero_relation()
                continue

            b_presence = float(role_scores['presence'][b_id]['rate'])
            pair_presence_gate = clamp((min(a_presence, b_presence) - 0.014) / 0.096, 0.0, 1.0)

            legend_hard = a_name in set(legend_by_name.get(b_name, {}).get('list_hard_heroes') or [])
            weak_forward = weak_hints.get(f'{b_name}|{a_name}')
            weak_reverse = weak_hints.get(f'{a_name}|{b_name}')
            ban_support_entry = ban_pressure_stats.get(b_name, {})

            counter_raw = 0.0
            counter_conf = 0.0
            counter_sample = 0
            counter_sources: dict = {}
            if legend_hard:
                counter_raw += 0.92
                counter_conf += 0.58 + pair_presence_gate * 0.12
                counter_sample += 28
                counter_sources['legendHard'] = True
            if weak_forward:
                games = int(weak_forward.get('games') or 0)
                hint = float(weak_forward.get('hintScore') or 0.0)
                weak_conf = sample_confidence(games, 144) * (0.30 + pair_presence_gate * 0.55)
                weak_raw = clamp(hint * 2.25, 0.0, 0.34)
                counter_raw += weak_raw * (0.56 if legend_hard else 1.0)
                counter_conf += weak_conf * (0.50 if legend_hard else 0.64)
                counter_sample += games
                counter_sources['weakHint'] = {
                    'games': games,
                    'hintScore': round4(hint),
                    'weight': round4(weak_raw),
                }
            if weak_reverse:
                games = int(weak_reverse.get('games') or 0)
                hint = float(weak_reverse.get('hintScore') or 0.0)
                reverse_conf = sample_confidence(games, 144) * (0.24 + pair_presence_gate * 0.40)
                reverse_raw = clamp(hint * 1.7, 0.0, 0.22) * reverse_conf * 0.70
                counter_raw -= reverse_raw
                counter_sources['reverseWeakHint'] = {
                    'games': games,
                    'hintScore': round4(hint),
                    'penalty': round4(reverse_raw),
                }
            if counter_raw > 0:
                ban_pressure = float(ban_support_entry.get('banPressureScore') or 0.0)
                ban_count = int(ban_support_entry.get('banCount') or 0)
                if ban_pressure > 0 and ban_count > 0:
                    bonus_cap = 0.05 if legend_hard else 0.03
                    bonus = clamp(ban_pressure * 0.24, 0.0, bonus_cap)
                    counter_raw += bonus
                    counter_conf += sample_confidence(ban_count, 52) * 0.08
                    counter_sample += ban_count
                    counter_sources['banPressure'] = {
                        'count': ban_count,
                        'banPressureScore': round4(ban_pressure),
                        'bonus': round4(bonus),
                    }
            counter_cap = 1.02 if legend_hard else 0.46
            counter_raw = clamp(counter_raw, 0.0, counter_cap)
            if counter_raw <= 0:
                counter_entry = zero_relation()
            else:
                counter_conf = clamp(counter_conf * (0.62 + pair_presence_gate * 0.38), 0.0, 0.93)
                counter_score = counter_raw * counter_conf
                if counter_conf < 0.999:
                    confidence_weakened += 1
                counter_nonzero += 1
                counter_entry = {
                    'score': round4(counter_score),
                    'rawScore': round4(counter_raw),
                    'confidence': round4(counter_conf),
                    'sample': int(counter_sample),
                    'cap': round4(counter_cap),
                    'sources': counter_sources,
                }
            counter_row[b_id] = counter_entry

            legend_syn = b_name in set(legend_by_name.get(a_name, {}).get('list_with_heroes') or [])
            pair_key = '|'.join(sorted([a_name, b_name]))
            pair_entry = pair_stats.get(pair_key)
            package_entries = sorted(pair_package_support.get(tuple(sorted([a_id, b_id])), []), key=lambda item: item['support'], reverse=True)

            synergy_raw = 0.0
            synergy_conf = 0.0
            synergy_sample = 0
            synergy_sources: dict = {}
            if legend_syn:
                synergy_raw += 0.78
                synergy_conf += 0.54 + pair_presence_gate * 0.10
                synergy_sample += 22
                synergy_sources['legendWith'] = True
            if pair_entry:
                games = int(pair_entry.get('games') or 0)
                lift = max(0.0, float(pair_entry.get('lift') or 0.0))
                if games > 0 and lift > 0:
                    lift_gate = clamp((lift - 0.05) / 0.34, 0.0, 1.0)
                    pair_conf = sample_confidence(games, 168) * (0.32 + pair_presence_gate * 0.58) * (0.45 + lift_gate * 0.55)
                    pair_raw = clamp(lift * 0.94, 0.0, 0.42)
                    synergy_raw += pair_raw * (0.62 if legend_syn else 1.0)
                    synergy_conf += pair_conf * (0.54 if legend_syn else 0.66)
                    synergy_sample += games
                    synergy_sources['pairLift'] = {
                        'games': games,
                        'lift': round4(lift),
                        'weight': round4(pair_raw),
                    }
            if package_entries:
                top_entries = package_entries[:2]
                package_support = 0.0
                package_sample = 0
                for index, item in enumerate(top_entries):
                    factor = 1.0 if index == 0 else 0.55
                    package_support += item['support'] * factor
                    package_sample += int(item['games'])
                package_support = clamp(package_support, 0.0, 0.26)
                package_conf = sample_confidence(package_sample, 120) * (0.24 + pair_presence_gate * 0.52)
                synergy_raw += package_support * (0.58 if legend_syn else 0.82)
                synergy_conf += package_conf * 0.36
                synergy_sample += package_sample
                synergy_sources['packageLift'] = {
                    'games': package_sample,
                    'topLift': round4(top_entries[0]['lift']),
                    'weight': round4(package_support),
                }
            synergy_cap = 0.96 if legend_syn else 0.58
            synergy_raw = clamp(synergy_raw, 0.0, synergy_cap)
            if synergy_raw <= 0:
                synergy_entry = zero_relation()
            else:
                synergy_conf = clamp(synergy_conf * (0.58 + pair_presence_gate * 0.42), 0.0, 0.94)
                synergy_score = synergy_raw * synergy_conf
                if synergy_conf < 0.999:
                    confidence_weakened += 1
                synergy_nonzero += 1
                synergy_entry = {
                    'score': round4(synergy_score),
                    'rawScore': round4(synergy_raw),
                    'confidence': round4(synergy_conf),
                    'sample': int(synergy_sample),
                    'cap': round4(synergy_cap),
                    'sources': synergy_sources,
                }
            synergy_row[b_id] = synergy_entry

        counter_matrix['counterMatrix'][a_id] = counter_row
        synergy_matrix['synergyMatrix'][a_id] = synergy_row

    counter_matrix['buildSummary'] = {
        'baselineHeroCount': hero_count,
        'nonzeroRelations': counter_nonzero,
        'confidenceWeakenedRelations': confidence_weakened,
    }
    synergy_matrix['buildSummary'] = {
        'baselineHeroCount': hero_count,
        'nonzeroRelations': synergy_nonzero,
        'confidenceWeakenedRelations': confidence_weakened,
    }
    role_scores['buildSummary'] = {
        'baselineHeroCount': hero_count,
        'lowPickSuppressedHeroes': low_pick_suppressed,
    }

    runtime_overlay = build_runtime_overlay(compiled_heroes, counter_matrix, synergy_matrix, role_scores)

    write_json(MATCHUP_OUT, counter_matrix)
    write_json(SYNERGY_OUT, synergy_matrix)
    write_json(ROLE_OUT, role_scores)
    write_json(OVERLAY_OUT, runtime_overlay)

    summary = {
        'baseline_hero_count': hero_count,
        'counter_matrix_size': hero_count * hero_count,
        'synergy_matrix_size': hero_count * hero_count,
        'counter_nonzero_relations': counter_nonzero,
        'synergy_nonzero_relations': synergy_nonzero,
        'confidence_weakened_relations': confidence_weakened,
        'low_pick_suppressed_heroes': low_pick_suppressed,
        'overlay_avg_helps_with': runtime_overlay['meta']['buildSummary']['avgHelpsWith'],
        'overlay_avg_good_vs': runtime_overlay['meta']['buildSummary']['avgGoodVs'],
        'overlay_avg_bad_vs': runtime_overlay['meta']['buildSummary']['avgBadVs'],
        'overlay_unresolved_ids': runtime_overlay['meta']['buildSummary']['unresolvedCount'],
        'outputs': [
            str(MATCHUP_OUT.relative_to(ROOT)).replace('\\', '/'),
            str(SYNERGY_OUT.relative_to(ROOT)).replace('\\', '/'),
            str(ROLE_OUT.relative_to(ROOT)).replace('\\', '/'),
            str(OVERLAY_OUT.relative_to(ROOT)).replace('\\', '/'),
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

