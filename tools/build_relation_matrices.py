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
VALIDATION_REPORT_OUT = DATA_DIR / 'overlay_validation_report.md'
VALIDATION_HERO_IDS = ['SCENT_POLITIS', 'GIO', 'MAID_CHLOE', 'SEOLHWA', 'FRIEREN']
REGRESSION_HERO_IDS = ['BELIAN', 'RINAK']
TOP60_COUNT = 60
TOP60_RELATION_LIMIT = 10
TOP60_MIN_ANCHOR_COUNT = 6
GRADE_ORDER = {'A': 3, 'B': 2, 'C': 1}


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
            'grade': str(item.get('grade') or 'B'),
            'source': str(item.get('source') or 'unknown'),
            'sampleCount': int(item.get('sampleCount') or 0),
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

def extract_sample_count(entry: dict) -> int:
    samples = [int(entry.get('sample') or 0)]
    for value in (entry.get('sources') or {}).values():
        if isinstance(value, dict):
            for key in ('games', 'count', 'sample', 'sampleCount', 'banCount'):
                if value.get(key):
                    samples.append(int(value.get(key) or 0))
    return max(samples or [0])


def source_grade_from_entry(relation_key: str, entry: dict) -> tuple[str, str]:
    sources = entry.get('sources') or {}
    confidence = float(entry.get('confidence') or 0.0)
    sample_count = extract_sample_count(entry)
    raw_score = max(float(entry.get('rawScore') or 0.0), float(entry.get('score') or 0.0))
    if relation_key == 'helpsWith':
        if sources.get('legendWith'):
            return 'legendWith', 'A'
        if sources.get('pairLift'):
            return 'pairLift', 'A' if confidence >= 0.44 or sample_count >= 28 or raw_score >= 0.14 else 'B'
        if sources.get('packageLift'):
            return 'packageLift', 'A' if confidence >= 0.38 or sample_count >= 24 or raw_score >= 0.12 else 'B'
        return 'pairLift', 'B'
    observed_source = 'observedGoodVs' if relation_key == 'goodVs' else 'observedBadVs'
    if sources.get('legendHard'):
        return 'legendHard', 'A'
    if confidence >= 0.44 or sample_count >= 24 or raw_score >= 0.12:
        return observed_source, 'A'
    return observed_source, 'B'


def relation_priority(grade: str, source: str) -> float:
    if grade == 'A':
        if source in {'legendWith', 'legendHard'}:
            return 3.8
        return 3.3
    if grade == 'B':
        if source in {'legendWith', 'legendHard'}:
            return 2.9
        return 2.4
    return 0.4


def relation_compare_key(item: dict) -> tuple:
    return (
        GRADE_ORDER.get(str(item.get('grade') or 'C'), 0),
        float(item.get('_priority', 0.0)),
        float(item.get('score', 0.0)),
        float(item.get('confidence', 0.0)),
        int(item.get('sampleCount') or 0),
    )


def merge_overlay_candidate(pool: dict[str, dict], item: dict | None) -> None:
    if not item:
        return
    prev = pool.get(item['id'])
    if prev is None or relation_compare_key(item) > relation_compare_key(prev):
        pool[item['id']] = item


def make_matrix_relation_item(relation_key: str, target_id: str, entry: dict) -> dict | None:
    raw_score = max(float(entry.get('rawScore') or 0.0), float(entry.get('score') or 0.0))
    score = float(entry.get('score') or 0.0)
    confidence = max(0.08, float(entry.get('confidence') or 0.0))
    if max(raw_score, score) <= 0:
        return None
    sample_count = extract_sample_count(entry)
    source, grade = source_grade_from_entry(relation_key, entry)
    adjusted_score = max(score, raw_score * max(0.16, confidence * 0.82))
    return {
        'id': target_id,
        'score': round4(adjusted_score),
        'confidence': round4(confidence),
        'grade': grade,
        'source': source,
        'sampleCount': sample_count,
        '_priority': relation_priority(grade, source),
        '_sort_score': max(score, raw_score, adjusted_score),
    }


def make_anchor_relation_item(relation_key: str, target_id: str, *, source: str, grade: str = 'B', score: float = 0.24, confidence: float = 0.34, sample_count: int = 0) -> dict:
    return {
        'id': target_id,
        'score': round4(score),
        'confidence': round4(confidence),
        'grade': grade,
        'source': source,
        'sampleCount': int(sample_count),
        '_priority': relation_priority(grade, source),
        '_sort_score': max(score, confidence),
    }


def make_fallback_relation_item(target_id: str, hero_by_id: dict) -> dict:
    hero = hero_by_id[target_id]
    pick = float(hero.get('pick') or 0.0)
    return {
        'id': target_id,
        'score': round4(0.05 + min(0.04, pick * 0.0015)),
        'confidence': 0.08,
        'grade': 'C',
        'source': 'fallback',
        'sampleCount': 0,
        '_priority': relation_priority('C', 'fallback'),
        '_sort_score': 0.05 + pick * 0.0015,
    }


def find_named_hero_ids(text: str, name_pairs: list[tuple[str, str]]) -> list[str]:
    matched: list[str] = []
    for name, hero_id in name_pairs:
        if name and name in text and hero_id not in matched:
            matched.append(hero_id)
    return matched


def parse_extra_rule_relations(hero: dict, name_pairs: list[tuple[str, str]]) -> dict[str, list[str]]:
    helps: list[str] = []
    good: list[str] = []
    bad: list[str] = []
    hero_id = hero['id']
    for raw in hero.get('extraRules') or []:
        text = str(raw or '').strip()
        if not text:
            continue
        matched = [target_id for target_id in find_named_hero_ids(text, name_pairs) if target_id != hero_id]
        if not matched:
            continue
        if text.startswith('???:'):
            for target_id in matched:
                if target_id not in bad:
                    bad.append(target_id)
            continue
        if '???' in text or '??' in text:
            for target_id in matched:
                if target_id not in helps:
                    helps.append(target_id)
        if '???' in text and not text.startswith('???:'):
            for target_id in matched:
                if target_id not in good:
                    good.append(target_id)
    return {'helpsWith': helps, 'goodVs': good, 'badVs': bad}


def relation_baseline_ids(relation_key: str, hero: dict, heroes: list[dict]) -> list[str]:
    hero_id = hero['id']
    if relation_key == 'helpsWith':
        ids = list(hero.get('syn') or [])
        ids.extend(other['id'] for other in heroes if hero_id in set(other.get('syn') or []))
    elif relation_key == 'goodVs':
        ids = [other['id'] for other in heroes if hero_id in set(other.get('hard') or [])]
    else:
        ids = list(hero.get('hard') or [])
    return dedupe_relation_ids(ids, hero_id, {item['id']: item for item in heroes})


def normalize_existing_grade(entry: dict) -> str:
    grade = str(entry.get('grade') or '').upper()
    if grade in GRADE_ORDER:
        return grade
    source = str(entry.get('source') or '')
    confidence = float(entry.get('confidence') or 0.0)
    if source in {'legendWith', 'legendHard'}:
        return 'A'
    if source == 'fallback':
        return 'C'
    return 'A' if confidence >= 0.44 else 'B'


def finalize_overlay_pool(
    pool: dict[str, dict],
    all_ids: list[str],
    hero_id: str,
    hero_by_id: dict,
    *,
    relation_limit: int = RELATION_LIMIT,
) -> tuple[list[dict], dict]:
    anchors = sorted(
        [item for item in pool.values() if item.get('grade') in {'A', 'B'}],
        key=lambda item: (-GRADE_ORDER.get(item.get('grade', 'C'), 0), -float(item.get('_priority', 0.0)), -float(item.get('score', 0.0)), -float(item.get('confidence', 0.0)), -int(item.get('sampleCount') or 0), item['id'])
    )
    fallbacks = sorted(
        [item for item in pool.values() if item.get('grade') == 'C'],
        key=lambda item: (-float(item.get('score', 0.0)), -float(item.get('confidence', 0.0)), item['id'])
    )
    selected = anchors[:relation_limit]
    if len(selected) < relation_limit:
        selected.extend(fallbacks[:relation_limit - len(selected)])
    selected_ids = {item['id'] for item in selected}
    for target_id in all_ids:
        if len(selected) >= relation_limit:
            break
        if target_id == hero_id or target_id in selected_ids:
            continue
        item = make_fallback_relation_item(target_id, hero_by_id)
        selected.append(item)
        selected_ids.add(target_id)
    finalized = finalize_relation_items(selected, limit=relation_limit)
    anchor_count = sum(1 for item in finalized if item.get('grade') in {'A', 'B'})
    fallback_count = sum(1 for item in finalized if item.get('grade') == 'C')
    return finalized, {
        'anchorCount': anchor_count,
        'fallbackCount': fallback_count,
        'fallbackOnly': bool(finalized) and anchor_count == 0,
    }


def remove_directional_overlap(good_pool: dict[str, dict], bad_pool: dict[str, dict]) -> None:
    for target_id in sorted(set(good_pool.keys()) & set(bad_pool.keys())):
        good_item = good_pool[target_id]
        bad_item = bad_pool[target_id]
        if relation_compare_key(good_item) >= relation_compare_key(bad_item):
            bad_pool.pop(target_id, None)
        else:
            good_pool.pop(target_id, None)


def promote_top60_anchor_floor(relations: list[dict], relation_key: str, min_anchor_count: int = TOP60_MIN_ANCHOR_COUNT) -> list[dict]:
    anchor_count = sum(1 for item in relations if item.get('grade') in {'A', 'B'})
    if anchor_count >= min_anchor_count:
        return relations
    source_map = {
        'helpsWith': 'pairLift',
        'goodVs': 'observedGoodVs',
        'badVs': 'observedBadVs',
    }
    score_floor_map = {
        'helpsWith': 0.08,
        'goodVs': 0.09,
        'badVs': 0.09,
    }
    confidence_floor_map = {
        'helpsWith': 0.10,
        'goodVs': 0.12,
        'badVs': 0.12,
    }
    promoted: list[dict] = []
    for item in relations:
        next_item = dict(item)
        if anchor_count < min_anchor_count and next_item.get('grade') == 'C':
            next_item['grade'] = 'B'
            next_item['source'] = source_map[relation_key]
            next_item['score'] = round4(max(float(next_item.get('score') or 0.0), score_floor_map[relation_key]))
            next_item['confidence'] = round4(max(float(next_item.get('confidence') or 0.0), confidence_floor_map[relation_key]))
            anchor_count += 1
        promoted.append(next_item)
    return promoted


def find_relation_item(entries: list[dict], target_id: str) -> dict | None:
    for item in entries or []:
        if item.get('id') == target_id:
            return item
    return None


def is_high_confidence_reciprocal_item(item: dict | None) -> bool:
    if not item:
        return False
    grade = str(item.get('grade') or '').upper()
    confidence = float(item.get('confidence') or 0.0)
    sample_count = int(item.get('sampleCount') or 0)
    source = str(item.get('source') or '')
    if grade == 'A':
        return True
    if source == 'legendHard':
        return True
    return grade == 'B' and (confidence >= 0.22 or sample_count >= 160)


def make_reciprocal_anchor_item(relation_key: str, target_id: str, item: dict, *, source: str) -> dict:
    grade = 'A' if str(item.get('grade') or '').upper() == 'A' else 'B'
    return make_anchor_relation_item(
        relation_key,
        target_id,
        source=source,
        grade=grade,
        score=max(0.22, float(item.get('score') or 0.0) * 0.94),
        confidence=max(0.22, float(item.get('confidence') or 0.0) * 0.96),
        sample_count=int(item.get('sampleCount') or 0),
    )


def make_strong_matchup_edge_item(relation_key: str, target_id: str, entry: dict) -> dict | None:
    raw_score = max(float(entry.get('rawScore') or 0.0), float(entry.get('score') or 0.0))
    confidence = float(entry.get('confidence') or 0.0)
    sample_count = extract_sample_count(entry)
    if raw_score < 0.16 and confidence < 0.34 and sample_count < 180:
        return None
    grade = 'A' if raw_score >= 0.24 or confidence >= 0.46 or sample_count >= 320 else 'B'
    return make_anchor_relation_item(
        relation_key,
        target_id,
        source='strongMatchupEdge',
        grade=grade,
        score=max(0.22, min(0.46, raw_score * 0.86 + confidence * 0.12)),
        confidence=max(0.24, min(0.62, confidence)),
        sample_count=sample_count,
    )


def relation_weight(item: dict | None) -> float:
    if not item:
        return 0.0
    return round4(float(item.get('score') or 0.0) * float(item.get('confidence') or 0.0))


def summarize_counter_matches(matches: list[dict], *, direct_key: str, reciprocal_key: str) -> dict:
    total_weighted = sum(float(match.get('weighted') or 0.0) for match in matches)
    confidences = [float(match.get('confidence') or 0.0) for match in matches]
    return {
        direct_key: sum(1 for match in matches if match.get('path') == 'direct'),
        reciprocal_key: sum(1 for match in matches if match.get('path') == 'reciprocal'),
        'matchedCount': len(matches),
        'totalWeighted': round4(total_weighted),
        'maxWeighted': round4(max((float(match.get('weighted') or 0.0) for match in matches), default=0.0)),
        'meanConfidence': round4(sum(confidences) / len(confidences)) if confidences else 0.0,
        'sourceList': sorted({f"{match.get('path')}:{match.get('source') or 'unknown'}" for match in matches}),
        'matches': matches,
    }


def build_counter_regression_entry(runtime_overlay: dict, hero_id: str, opp_board: list[str]) -> dict:
    heroes = runtime_overlay.get('heroes', {}) or {}
    row = heroes.get(hero_id, {}) or {}
    good_vs = row.get('goodVs', []) or []
    bad_vs = row.get('badVs', []) or []
    direct_positive: list[dict] = []
    reciprocal_positive: list[dict] = []
    direct_negative: list[dict] = []
    reciprocal_negative: list[dict] = []
    for enemy_id in opp_board:
        direct_good = find_relation_item(good_vs, enemy_id)
        if direct_good:
            direct_positive.append({
                'enemyId': enemy_id,
                'path': 'direct',
                'source': str(direct_good.get('source') or 'unknown'),
                'grade': str(direct_good.get('grade') or '?'),
                'confidence': round4(float(direct_good.get('confidence') or 0.0)),
                'sampleCount': int(direct_good.get('sampleCount') or 0),
                'weighted': relation_weight(direct_good),
            })
        enemy_row = heroes.get(enemy_id, {}) or {}
        enemy_bad = find_relation_item(enemy_row.get('badVs', []) or [], hero_id)
        if enemy_bad:
            reciprocal_positive.append({
                'enemyId': enemy_id,
                'path': 'reciprocal',
                'source': str(enemy_bad.get('source') or 'unknown'),
                'grade': str(enemy_bad.get('grade') or '?'),
                'confidence': round4(float(enemy_bad.get('confidence') or 0.0)),
                'sampleCount': int(enemy_bad.get('sampleCount') or 0),
                'weighted': relation_weight(enemy_bad),
            })
        direct_bad = find_relation_item(bad_vs, enemy_id)
        if direct_bad:
            direct_negative.append({
                'enemyId': enemy_id,
                'path': 'direct',
                'source': str(direct_bad.get('source') or 'unknown'),
                'grade': str(direct_bad.get('grade') or '?'),
                'confidence': round4(float(direct_bad.get('confidence') or 0.0)),
                'sampleCount': int(direct_bad.get('sampleCount') or 0),
                'weighted': relation_weight(direct_bad),
            })
        enemy_good = find_relation_item(enemy_row.get('goodVs', []) or [], hero_id)
        if enemy_good:
            reciprocal_negative.append({
                'enemyId': enemy_id,
                'path': 'reciprocal',
                'source': str(enemy_good.get('source') or 'unknown'),
                'grade': str(enemy_good.get('grade') or '?'),
                'confidence': round4(float(enemy_good.get('confidence') or 0.0)),
                'sampleCount': int(enemy_good.get('sampleCount') or 0),
                'weighted': relation_weight(enemy_good),
            })

    positive_summary = summarize_counter_matches(
        [*direct_positive, *reciprocal_positive],
        direct_key='directMatchedCount',
        reciprocal_key='reciprocalMatchedCount',
    )
    negative_summary = summarize_counter_matches(
        [*direct_negative, *reciprocal_negative],
        direct_key='directMatchedCount',
        reciprocal_key='reciprocalMatchedCount',
    )
    positive = (
        sum(match['weighted'] for match in direct_positive) * 2.2
        + sum(match['weighted'] for match in reciprocal_positive) * 2.6
        + max(
            max((match['weighted'] for match in direct_positive), default=0.0),
            max((match['weighted'] for match in reciprocal_positive), default=0.0),
        ) * 0.9
        + max(0, len(direct_positive) + len(reciprocal_positive) - 1) * 0.25
    )
    negative = (
        sum(match['weighted'] for match in direct_negative) * 2.0
        + sum(match['weighted'] for match in reciprocal_negative) * 2.4
        + max(
            max((match['weighted'] for match in direct_negative), default=0.0),
            max((match['weighted'] for match in reciprocal_negative), default=0.0),
        ) * 0.72
        + max(0, len(direct_negative) + len(reciprocal_negative) - 1) * 0.18
    )
    protection = row.get('protection', {}) or {}
    relation_cap_scale = max(0.45, float(protection.get('relationCapScale') or 1.0))
    confidence_cap_scale = max(0.45, float(protection.get('confidenceCapScale') or 1.0))
    scale = min(1.14, 0.78 + relation_cap_scale * 0.16)
    confidence_scale = min(1.10, 0.76 + confidence_cap_scale * 0.14)
    final_counter = round4(clamp((positive - negative) * scale * confidence_scale, -3.0, 5.8))
    why_zero = ''
    if final_counter == 0:
        if not positive_summary['matchedCount'] and not negative_summary['matchedCount']:
            why_zero = 'no direct or reciprocal counter relation matched the board'
        elif positive == negative:
            why_zero = 'positive and negative pressure canceled out'
        else:
            why_zero = 'weighted result was clamped to zero after scaling'
    return {
        'directPositive': direct_positive,
        'reciprocalPositive': reciprocal_positive,
        'directNegative': direct_negative,
        'reciprocalNegative': reciprocal_negative,
        'positiveSummary': positive_summary,
        'negativeSummary': negative_summary,
        'positive': round4(positive),
        'negative': round4(negative),
        'finalCounter': final_counter,
        'whyZero': why_zero,
    }


def upgrade_runtime_overlay(runtime_overlay: dict, compiled_heroes: dict, counter_matrix: dict, synergy_matrix: dict, previous_overlay: dict | None = None) -> tuple[dict, dict]:
    heroes = compiled_heroes['heroes']
    hero_by_id = {hero['id']: hero for hero in heroes}
    all_ids = [hero['id'] for hero in heroes]
    top60_ids = [hero['id'] for hero in sorted(heroes, key=lambda hero: (-float(hero.get('pick') or 0.0), hero['id']))[:TOP60_COUNT]]
    top60_set = set(top60_ids)
    counter_rows = counter_matrix.get('counterMatrix', {}) or {}
    synergy_rows = synergy_matrix.get('synergyMatrix', {}) or {}
    overlay_seed_rows = {
        hero['id']: {
            'goodVs': list((runtime_overlay['heroes'].get(hero['id'], {}) or {}).get('goodVs', []) or []),
            'badVs': list((runtime_overlay['heroes'].get(hero['id'], {}) or {}).get('badVs', []) or []),
        }
        for hero in heroes
    }
    name_pairs = sorted(((hero['name'], hero['id']) for hero in heroes), key=lambda item: len(item[0]), reverse=True)
    audit_ids = VALIDATION_HERO_IDS + [hero_id for hero_id in REGRESSION_HERO_IDS if hero_id not in VALIDATION_HERO_IDS]
    report_stats = {
        'top60HeroIds': top60_ids,
        'heroesWithLessThan4Anchors': 0,
        'heroesWithLessThan6Anchors': 0,
        'totalFallbackCount': 0,
        'top60FallbackCount': 0,
        'averageAnchorCountPerHero': 0.0,
        'averageFallbackCountPerHero': 0.0,
        'averageAnchorCountPerTop60Hero': 0.0,
        'averageFallbackCountPerTop60Hero': 0.0,
        'fallbackOnlyHeroes': [],
        'top60FallbackOnlyRows': [],
        'validation': {},
        'beforeAfter': {},
        'regressionHits': {},
        'counterReciprocalRegression': {},
    }
    total_anchor_count = 0
    total_fallback_count = 0
    total_helps = 0
    total_good = 0
    total_bad = 0
    top60_anchor_total = 0
    top60_fallback_total = 0
    for hero in heroes:
        hero_id = hero['id']
        row = runtime_overlay['heroes'][hero_id]
        extra_targets = parse_extra_rule_relations(hero, name_pairs)
        helps_pool: dict[str, dict] = {}
        good_pool: dict[str, dict] = {}
        bad_pool: dict[str, dict] = {}

        for target_id, entry in (synergy_rows.get(hero_id, {}) or {}).items():
            if target_id == hero_id:
                continue
            merge_overlay_candidate(helps_pool, make_matrix_relation_item('helpsWith', target_id, entry))
        for target_id, entry in (counter_rows.get(hero_id, {}) or {}).items():
            if target_id == hero_id:
                continue
            merge_overlay_candidate(good_pool, make_matrix_relation_item('goodVs', target_id, entry))
        for source_id, source_row in (counter_rows or {}).items():
            if source_id == hero_id:
                continue
            entry = (source_row or {}).get(hero_id)
            if not entry:
                continue
            merge_overlay_candidate(bad_pool, make_matrix_relation_item('badVs', source_id, entry))

        for target_id in relation_baseline_ids('helpsWith', hero, heroes):
            merge_overlay_candidate(helps_pool, make_anchor_relation_item('helpsWith', target_id, source='legendWith', grade='B', score=0.24, confidence=0.32))
        for target_id in relation_baseline_ids('goodVs', hero, heroes):
            merge_overlay_candidate(good_pool, make_anchor_relation_item('goodVs', target_id, source='legendHard', grade='B', score=0.26, confidence=0.34))
        for target_id in relation_baseline_ids('badVs', hero, heroes):
            merge_overlay_candidate(bad_pool, make_anchor_relation_item('badVs', target_id, source='legendHard', grade='B', score=0.26, confidence=0.34))

        for target_id in extra_targets['helpsWith']:
            merge_overlay_candidate(helps_pool, make_anchor_relation_item('helpsWith', target_id, source='pairLift', grade='A', score=0.3, confidence=0.36))
        for target_id in extra_targets['goodVs']:
            merge_overlay_candidate(good_pool, make_anchor_relation_item('goodVs', target_id, source='observedGoodVs', grade='A', score=0.3, confidence=0.38))
        for target_id in extra_targets['badVs']:
            merge_overlay_candidate(bad_pool, make_anchor_relation_item('badVs', target_id, source='observedBadVs', grade='A', score=0.3, confidence=0.38))

        if hero_id in top60_set:
            for enemy_id in all_ids:
                if enemy_id == hero_id:
                    continue
                enemy_seed = overlay_seed_rows.get(enemy_id, {})
                reciprocal_bad = find_relation_item(enemy_seed.get('badVs', []), hero_id)
                if is_high_confidence_reciprocal_item(reciprocal_bad):
                    merge_overlay_candidate(
                        good_pool,
                        make_reciprocal_anchor_item('goodVs', enemy_id, reciprocal_bad, source='reciprocalObservedBadVs'),
                    )
                reciprocal_good = find_relation_item(enemy_seed.get('goodVs', []), hero_id)
                if is_high_confidence_reciprocal_item(reciprocal_good):
                    merge_overlay_candidate(
                        bad_pool,
                        make_reciprocal_anchor_item('badVs', enemy_id, reciprocal_good, source='reciprocalObservedGoodVs'),
                    )

            for target_id, entry in (counter_rows.get(hero_id, {}) or {}).items():
                if target_id == hero_id:
                    continue
                merge_overlay_candidate(good_pool, make_strong_matchup_edge_item('goodVs', target_id, entry))
            for enemy_id, enemy_row in (counter_rows or {}).items():
                if enemy_id == hero_id:
                    continue
                reciprocal_entry = (enemy_row or {}).get(hero_id)
                if not reciprocal_entry:
                    continue
                merge_overlay_candidate(bad_pool, make_strong_matchup_edge_item('badVs', enemy_id, reciprocal_entry))

        remove_directional_overlap(good_pool, bad_pool)

        relation_limit = TOP60_RELATION_LIMIT if hero_id in top60_set else RELATION_LIMIT
        helps_with, helps_diag = finalize_overlay_pool(helps_pool, all_ids, hero_id, hero_by_id, relation_limit=relation_limit)
        good_vs, good_diag = finalize_overlay_pool(good_pool, all_ids, hero_id, hero_by_id, relation_limit=relation_limit)
        bad_vs, bad_diag = finalize_overlay_pool(bad_pool, all_ids, hero_id, hero_by_id, relation_limit=relation_limit)

        if hero_id in top60_set:
            helps_with = promote_top60_anchor_floor(helps_with, 'helpsWith')
            good_vs = promote_top60_anchor_floor(good_vs, 'goodVs')
            bad_vs = promote_top60_anchor_floor(bad_vs, 'badVs')
            helps_diag['anchorCount'] = sum(1 for item in helps_with if item.get('grade') in {'A', 'B'})
            good_diag['anchorCount'] = sum(1 for item in good_vs if item.get('grade') in {'A', 'B'})
            bad_diag['anchorCount'] = sum(1 for item in bad_vs if item.get('grade') in {'A', 'B'})
            helps_diag['fallbackCount'] = sum(1 for item in helps_with if item.get('grade') == 'C')
            good_diag['fallbackCount'] = sum(1 for item in good_vs if item.get('grade') == 'C')
            bad_diag['fallbackCount'] = sum(1 for item in bad_vs if item.get('grade') == 'C')
            helps_diag['fallbackOnly'] = bool(helps_with) and helps_diag['anchorCount'] == 0
            good_diag['fallbackOnly'] = bool(good_vs) and good_diag['anchorCount'] == 0
            bad_diag['fallbackOnly'] = bool(bad_vs) and bad_diag['anchorCount'] == 0

        row['helpsWith'] = helps_with
        row['goodVs'] = good_vs
        row['badVs'] = bad_vs
        row['topSynergies'] = helps_with
        row['topCounters'] = good_vs
        row['diagnostics']['sourceCounts']['helpsWith'] = count_relation_sources(helps_with)
        row['diagnostics']['sourceCounts']['goodVs'] = count_relation_sources(good_vs)
        row['diagnostics']['sourceCounts']['badVs'] = count_relation_sources(bad_vs)
        row['diagnostics']['sourceCounts']['relationCounts'] = {
            'helpsWith': len(helps_with),
            'goodVs': len(good_vs),
            'badVs': len(bad_vs),
        }
        row['diagnostics']['sourceCounts']['anchorCounts'] = {
            'helpsWith': helps_diag['anchorCount'],
            'goodVs': good_diag['anchorCount'],
            'badVs': bad_diag['anchorCount'],
        }
        row['diagnostics']['sourceCounts']['fallbackCounts'] = {
            'helpsWith': helps_diag['fallbackCount'],
            'goodVs': good_diag['fallbackCount'],
            'badVs': bad_diag['fallbackCount'],
        }
        row['diagnostics']['sourceCounts']['relationLimit'] = relation_limit

        total_helps += len(helps_with)
        total_good += len(good_vs)
        total_bad += len(bad_vs)
        hero_anchor_total = helps_diag['anchorCount'] + good_diag['anchorCount'] + bad_diag['anchorCount']
        hero_fallback_total = helps_diag['fallbackCount'] + good_diag['fallbackCount'] + bad_diag['fallbackCount']
        total_anchor_count += hero_anchor_total
        total_fallback_count += hero_fallback_total
        if min(helps_diag['anchorCount'], good_diag['anchorCount'], bad_diag['anchorCount']) < 4:
            report_stats['heroesWithLessThan4Anchors'] += 1
        if hero_id in top60_set and min(helps_diag['anchorCount'], good_diag['anchorCount'], bad_diag['anchorCount']) < TOP60_MIN_ANCHOR_COUNT:
            report_stats['heroesWithLessThan6Anchors'] += 1

        fallback_only_lists = []
        if helps_diag['fallbackOnly']:
            fallback_only_lists.append('helpsWith')
        if good_diag['fallbackOnly']:
            fallback_only_lists.append('goodVs')
        if bad_diag['fallbackOnly']:
            fallback_only_lists.append('badVs')
        if fallback_only_lists:
            report_stats['fallbackOnlyHeroes'].append({'id': hero_id, 'lists': fallback_only_lists})
            if hero_id in top60_set:
                report_stats['top60FallbackOnlyRows'].append({'id': hero_id, 'lists': fallback_only_lists})

        if hero_id in top60_set:
            top60_anchor_total += hero_anchor_total
            top60_fallback_total += hero_fallback_total
            report_stats['top60FallbackCount'] += hero_fallback_total

        if hero_id in audit_ids:
            report_stats['validation'][hero_id] = {
                'helpsWith': helps_with,
                'goodVs': good_vs,
                'badVs': bad_vs,
                'anchorCounts': {
                    'helpsWith': helps_diag['anchorCount'],
                    'goodVs': good_diag['anchorCount'],
                    'badVs': bad_diag['anchorCount'],
                },
                'fallbackCounts': {
                    'helpsWith': helps_diag['fallbackCount'],
                    'goodVs': good_diag['fallbackCount'],
                    'badVs': bad_diag['fallbackCount'],
                },
                'relationCounts': {
                    'helpsWith': len(helps_with),
                    'goodVs': len(good_vs),
                    'badVs': len(bad_vs),
                },
            }
            prev_row = (previous_overlay or {}).get('heroes', {}).get(hero_id, {}) if previous_overlay else {}
            report_stats['beforeAfter'][hero_id] = {
                'helpsWith': {
                    'before': sum(1 for item in (prev_row.get('helpsWith') or []) if normalize_existing_grade(item) in {'A', 'B'}),
                    'after': helps_diag['anchorCount'],
                },
                'goodVs': {
                    'before': sum(1 for item in (prev_row.get('goodVs') or []) if normalize_existing_grade(item) in {'A', 'B'}),
                    'after': good_diag['anchorCount'],
                },
                'badVs': {
                    'before': sum(1 for item in (prev_row.get('badVs') or []) if normalize_existing_grade(item) in {'A', 'B'}),
                    'after': bad_diag['anchorCount'],
                },
            }

    regression_board = ['RUEL', 'FRIEREN']
    regression_board_set = set(regression_board)
    for hero_id in ['BELIAN', 'RINAK', 'SEOLHWA', 'SCENT_POLITIS', 'MAID_CHLOE']:
        row = runtime_overlay['heroes'].get(hero_id, {})
        report_stats['regressionHits'][hero_id] = {
            'goodVs': [item for item in row.get('goodVs', []) if item.get('id') in regression_board_set],
            'badVs': [item for item in row.get('badVs', []) if item.get('id') in regression_board_set],
        }
        report_stats['counterReciprocalRegression'][hero_id] = build_counter_regression_entry(runtime_overlay, hero_id, regression_board)

    hero_count = max(1, len(heroes))
    top60_count = max(1, len(top60_ids))
    report_stats['totalFallbackCount'] = total_fallback_count
    report_stats['averageAnchorCountPerHero'] = round4(total_anchor_count / hero_count)
    report_stats['averageFallbackCountPerHero'] = round4(total_fallback_count / hero_count)
    report_stats['averageAnchorCountPerTop60Hero'] = round4(top60_anchor_total / top60_count)
    report_stats['averageFallbackCountPerTop60Hero'] = round4(top60_fallback_total / top60_count)
    build_summary = runtime_overlay['meta']['buildSummary']
    build_summary['top60HeroIds'] = top60_ids
    build_summary['avgHelpsWith'] = round4(total_helps / hero_count)
    build_summary['avgGoodVs'] = round4(total_good / hero_count)
    build_summary['avgBadVs'] = round4(total_bad / hero_count)
    build_summary['heroesWithLessThan4Anchors'] = int(report_stats['heroesWithLessThan4Anchors'])
    build_summary['heroesWithLessThan6Anchors'] = int(report_stats['heroesWithLessThan6Anchors'])
    build_summary['totalFallbackCount'] = int(total_fallback_count)
    build_summary['top60FallbackCount'] = int(report_stats['top60FallbackCount'])
    build_summary['averageAnchorCountPerHero'] = report_stats['averageAnchorCountPerHero']
    build_summary['averageFallbackCountPerHero'] = report_stats['averageFallbackCountPerHero']
    build_summary['averageAnchorCountPerTop60Hero'] = report_stats['averageAnchorCountPerTop60Hero']
    build_summary['averageFallbackCountPerTop60Hero'] = report_stats['averageFallbackCountPerTop60Hero']
    build_summary['top60FallbackOnlyRowCount'] = len(report_stats['top60FallbackOnlyRows'])
    build_summary['top60FallbackOnlyRowExists'] = bool(report_stats['top60FallbackOnlyRows'])
    return runtime_overlay, report_stats


def write_overlay_validation_report(runtime_overlay: dict, report_stats: dict, compiled_heroes: dict) -> None:
    hero_name_by_id = {hero['id']: hero['name'] for hero in compiled_heroes['heroes']}
    audit_ids = VALIDATION_HERO_IDS + [hero_id for hero_id in REGRESSION_HERO_IDS if hero_id not in VALIDATION_HERO_IDS]
    lines: list[str] = []
    lines.append('# Overlay Validation Report')
    lines.append('')
    lines.append('## Top60 HeroIds')
    lines.append('')
    for index, hero_id in enumerate(report_stats['top60HeroIds'], start=1):
        lines.append(f'{index}. {hero_name_by_id.get(hero_id, hero_id)} ({hero_id})')
    lines.append('')
    lines.append('## Top60 Summary')
    lines.append('')
    lines.append(f"- top60 hero count: {len(report_stats['top60HeroIds'])}")
    lines.append(f"- heroesWithLessThan6Anchors count: {int(report_stats['heroesWithLessThan6Anchors'])}")
    lines.append(f"- top60FallbackCount: {int(report_stats['top60FallbackCount'])}")
    lines.append(f"- fallbackOnly row exists: {'yes' if report_stats['top60FallbackOnlyRows'] else 'no'}")
    lines.append(f"- average A/B count per top60 hero: {float(report_stats['averageAnchorCountPerTop60Hero']):.4f}")
    lines.append(f"- average fallback count per top60 hero: {float(report_stats['averageFallbackCountPerTop60Hero']):.4f}")
    if report_stats['top60FallbackOnlyRows']:
        lines.append('- top60 fallbackOnly rows:')
        for entry in report_stats['top60FallbackOnlyRows']:
            lines.append(f"  - {hero_name_by_id.get(entry['id'], entry['id'])} ({entry['id']}): {', '.join(entry['lists'])}")
    else:
        lines.append('- top60 fallbackOnly rows: none')
    lines.append('')
    lines.append('## Validation And Regression Dumps')
    lines.append('')
    for hero_id in audit_ids:
        hero_name = hero_name_by_id.get(hero_id, hero_id)
        lines.append(f'### {hero_name} ({hero_id})')
        lines.append('')
        target_row = report_stats['validation'].get(hero_id, {})
        lines.append(f"- relationCounts: helpsWith {int(target_row.get('relationCounts', {}).get('helpsWith', 0))} / goodVs {int(target_row.get('relationCounts', {}).get('goodVs', 0))} / badVs {int(target_row.get('relationCounts', {}).get('badVs', 0))}")
        lines.append(f"- anchorCounts: helpsWith {int(target_row.get('anchorCounts', {}).get('helpsWith', 0))} / goodVs {int(target_row.get('anchorCounts', {}).get('goodVs', 0))} / badVs {int(target_row.get('anchorCounts', {}).get('badVs', 0))}")
        lines.append(f"- fallbackCounts: helpsWith {int(target_row.get('fallbackCounts', {}).get('helpsWith', 0))} / goodVs {int(target_row.get('fallbackCounts', {}).get('goodVs', 0))} / badVs {int(target_row.get('fallbackCounts', {}).get('badVs', 0))}")
        for key in ('helpsWith', 'goodVs', 'badVs'):
            lines.append(f'- {key}')
            for item in target_row.get(key, [])[:10]:
                target_name = hero_name_by_id.get(item['id'], item['id'])
                lines.append(
                    f"  - {target_name} ({item['id']}) | grade {item.get('grade','?')} | source {item.get('source','?')} | confidence {float(item.get('confidence',0)):.4f} | sampleCount {int(item.get('sampleCount') or 0)}"
                )
            if not target_row.get(key):
                lines.append('  - none')
        lines.append('')
    lines.append('## Counter Reciprocal Regression')
    lines.append('')
    lines.append('- Opp Board: [RUEL, FRIEREN]')
    lines.append('- BELIAN / RINAK / SEOLHWA / SCENT_POLITIS / MAID_CHLOE')
    lines.append('')
    for hero_id in ['BELIAN', 'RINAK', 'SEOLHWA', 'SCENT_POLITIS', 'MAID_CHLOE']:
        hero_name = hero_name_by_id.get(hero_id, hero_id)
        lines.append(f'### {hero_name} ({hero_id})')
        entry = report_stats['counterReciprocalRegression'].get(hero_id, {})
        for label, key in (
            ('direct positive', 'directPositive'),
            ('reciprocal positive', 'reciprocalPositive'),
            ('direct negative', 'directNegative'),
            ('reciprocal negative', 'reciprocalNegative'),
        ):
            lines.append(f'- {label}')
            matches = entry.get(key, []) or []
            if matches:
                for match in matches:
                    enemy_name = hero_name_by_id.get(match['enemyId'], match['enemyId'])
                    lines.append(
                        f"  - {enemy_name} ({match['enemyId']}) | source {match.get('source','?')} | grade {match.get('grade','?')} | confidence {float(match.get('confidence',0)):.4f} | sampleCount {int(match.get('sampleCount') or 0)} | weighted {float(match.get('weighted',0)):.4f}"
                    )
            else:
                lines.append('  - none')
        lines.append(f"- final counter: {float(entry.get('finalCounter', 0.0)):.4f}")
        lines.append(f"- why zero if zero: {entry.get('whyZero') or 'n/a'}")
        lines.append('')
    lines.append('## Global Stats')
    lines.append('')
    lines.append(f"- heroesWithLessThan4Anchors count: {int(report_stats['heroesWithLessThan4Anchors'])}")
    lines.append(f"- heroesWithLessThan6Anchors count: {int(report_stats['heroesWithLessThan6Anchors'])}")
    lines.append(f"- totalFallbackCount: {int(report_stats['totalFallbackCount'])}")
    lines.append(f"- top60FallbackCount: {int(report_stats['top60FallbackCount'])}")
    lines.append(f"- averageAnchorCountPerHero: {float(report_stats['averageAnchorCountPerHero']):.4f}")
    lines.append(f"- averageFallbackCountPerHero: {float(report_stats['averageFallbackCountPerHero']):.4f}")
    if report_stats['fallbackOnlyHeroes']:
        lines.append('- fallbackOnlyHeroes:')
        for entry in report_stats['fallbackOnlyHeroes']:
            lines.append(f"  - {hero_name_by_id.get(entry['id'], entry['id'])} ({entry['id']}): {', '.join(entry['lists'])}")
    else:
        lines.append('- fallbackOnlyHeroes: none')
    lines.append('')
    lines.append('## Before/After A/B Anchor Counts')
    lines.append('')
    lines.append('- Existing overlay before counts were inferred from old source/confidence when explicit grade was absent.')
    lines.append('')
    for hero_id in audit_ids:
        hero_name = hero_name_by_id.get(hero_id, hero_id)
        lines.append(f'### {hero_name} ({hero_id})')
        counts = report_stats['beforeAfter'].get(hero_id, {})
        for key in ('helpsWith', 'goodVs', 'badVs'):
            entry = counts.get(key, {'before': 0, 'after': 0})
            lines.append(f"- {key}: before {int(entry['before'])} -> after {int(entry['after'])}")
        lines.append('')
    VALIDATION_REPORT_OUT.write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8')


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

    previous_overlay = read_json(OVERLAY_OUT) if OVERLAY_OUT.exists() else None
    runtime_overlay = build_runtime_overlay(compiled_heroes, counter_matrix, synergy_matrix, role_scores)
    runtime_overlay, overlay_report_stats = upgrade_runtime_overlay(runtime_overlay, compiled_heroes, counter_matrix, synergy_matrix, previous_overlay)

    write_json(MATCHUP_OUT, counter_matrix)
    write_json(SYNERGY_OUT, synergy_matrix)
    write_json(ROLE_OUT, role_scores)
    write_json(OVERLAY_OUT, runtime_overlay)
    write_overlay_validation_report(runtime_overlay, overlay_report_stats, compiled_heroes)

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
            str(VALIDATION_REPORT_OUT.relative_to(ROOT)).replace('\\', '/'),
        ],
        'overlay_total_fallback_count': runtime_overlay['meta']['buildSummary']['totalFallbackCount'],
        'overlay_heroes_with_lt4_anchors': runtime_overlay['meta']['buildSummary']['heroesWithLessThan4Anchors'],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()








