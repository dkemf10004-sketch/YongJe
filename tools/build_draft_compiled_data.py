
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = next(ROOT.glob('**/*v2_merged.html'))
MD_PATH = next(ROOT.glob('**/hero_rules22.md'))
LEGEND_PATH = next(ROOT.glob('**/hero_full_legend.json'))
BATTLE_PATH = next(ROOT.glob('**/battle_accounts_merged.json'))
OUTPUT_DIR = HTML_PATH.parent
COMPILED_HEROES_PATH = OUTPUT_DIR / 'compiled_heroes.json'
COMPILED_PATTERNS_PATH = OUTPUT_DIR / 'compiled_patterns.json'
COMPILED_DIFF_PATH = OUTPUT_DIR / 'compiled_diff_report.md'


def read_text(path: Path) -> str:
    return path.read_text(encoding='utf-8-sig')


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding='utf-8', newline='')


def hero_key(value: str) -> str:
    return re.sub(r'[^0-9a-z가-힣]', '', re.sub(r'[\s\-]+', '', (value or '').strip().lower()))


def generated_hero_id(name: str) -> str:
    raw = re.sub(r'[^0-9A-Z가-힣]+', '_', (name or '').strip().upper()).strip('_')
    return f'EXT_{raw}'


def unique_preserve(items):
    seen, out = set(), []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def normalize_set_list(values):
    return unique_preserve([str(v).strip() for v in values or [] if str(v).strip()])


def parse_html_heroes(html_text: str):
    start = html_text.index('const HEROES = [')
    end = html_text.index('function heroKey(value){', start)
    block = html_text[start:end]
    array_match = re.search(r'const HEROES = \[(.*?)\];\s*const HERO_BY_ID', block, re.S)
    alias_match = re.search(r'const HERO_ID_ALIASES = \{(.*?)\};', block, re.S)
    if not array_match:
        raise RuntimeError('Failed to parse HEROES block')
    heroes = []
    for m in re.finditer(r"\{id:'([^']+)',\s*name:'([^']+)'(.*?)\}", array_match.group(1), re.S):
        note_match = re.search(r"note:'([^']*)'", m.group(3))
        heroes.append({'id': m.group(1), 'name': m.group(2), 'note': note_match.group(1) if note_match else ''})
    alias_map = {}
    if alias_match:
        for key, value in re.findall(r"([A-Za-z0-9_가-힣&\- ]+):'([^']+)'", alias_match.group(1)):
            alias_map[key.strip()] = value
    return heroes, alias_map


def parse_md_aliases(md_text: str):
    match = re.search(r'## 2\) 이름 정규화 / 별칭 규칙\n(.*?)(?:\n## |\Z)', md_text, re.S)
    aliases = {}
    if not match:
        return aliases
    for line in match.group(1).splitlines():
        m = re.match(r'-\s*(.+?)\s*->\s*(.+?)\s*$', line.strip())
        if m:
            aliases[m.group(1).strip()] = m.group(2).strip()
    return aliases


def parse_md_profiles(md_text: str):
    match = re.search(r'## 6\) 통합 영웅 프로필\n(.*?)(?:\n## |\Z)', md_text, re.S)
    if not match:
        return 0, {}, None
    body = match.group(1)
    profiles = {}
    headings = list(re.finditer(r'(?m)^###\s+(.+?)\s*$', body))
    for idx, heading in enumerate(headings):
        name = heading.group(1).strip()
        start = heading.end()
        end = headings[idx + 1].start() if idx + 1 < len(headings) else len(body)
        profiles[name] = body[start:end].strip('\n')
    return len(headings), profiles, match.span()


def parse_legend():
    raw = json.loads(read_text(LEGEND_PATH))
    seen, result = set(), []
    for row in raw:
        name = str(row.get('hero_name') or '').strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append({
            'name': name,
            'pick': float(row.get('table_pick_rate') or row.get('pick') or 0),
            'win': float(row.get('table_win_rate') or row.get('win') or 0),
            'ban': float(row.get('table_ban_rate') or row.get('ban') or 0),
            'hard_names': unique_preserve([str(v).strip() for v in row.get('list_hard_heroes') or [] if str(v).strip()]),
            'syn_names': unique_preserve([str(v).strip() for v in row.get('list_with_heroes') or [] if str(v).strip()]),
            'sets': normalize_set_list([(item.get('set_name') if isinstance(item, dict) else item) for item in (row.get('list_top_sets') or [])]),
        })
    return result


def extract_preserved_notes(block: str):
    if not block:
        return []
    notes = []
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith('### '):
            continue
        if any(token in line for token in ('픽률:', '승률:', '밴률:')):
            continue
        if line.startswith('- 최신 메타 수치') or line.startswith('- 최신 baseline 수치'):
            continue
        if line.startswith('- 상대하기 어려운 영웅') or line.startswith('- 함께 사용된 영웅') or line.startswith('- 선호 세트'):
            continue
        if line.startswith('- legend baseline 관계') or line.startswith('- source flags'):
            continue
        if line.startswith('- 개인 데이터') or line.startswith('- 별도 텍스트 규칙'):
            continue
        notes.append(re.sub(r'^-\s*', '', line))
    return unique_preserve(notes)


def derive_tags(notes):
    joined = ' '.join(notes)
    tags = []
    if any(token in joined for token in ('선턴', '선픽', '오픈')):
        tags.append('선턴잡이')
    if any(token in joined for token in ('속기', '속도 경쟁')):
        tags.append('속기각')
    if '하르세티' in joined:
        tags.append('하르세티 리스크')
    if '밴가드' in joined:
        tags.append('밴가드')
    return tags

def build_baseline(legend_entries, html_notes, md_profiles, html_id_by_name, battle_canonical_names):
    id_by_name = {entry['name']: html_id_by_name.get(entry['name'], generated_hero_id(entry['name'])) for entry in legend_entries}
    baseline = []
    for entry in legend_entries:
        name = entry['name']
        preserved_notes = extract_preserved_notes(md_profiles.get(name, ''))
        note = html_notes.get(name) or (preserved_notes[0] if preserved_notes else '')
        baseline.append({
            'id': id_by_name[name],
            'name': name,
            'pick': round(entry['pick'], 2),
            'win': round(entry['win'], 2),
            'ban': round(entry['ban'], 2),
            'hard': [id_by_name[n] for n in entry['hard_names'] if n in id_by_name],
            'syn': [id_by_name[n] for n in entry['syn_names'] if n in id_by_name],
            'sets': normalize_set_list(entry['sets']),
            'tags': derive_tags(preserved_notes),
            'sourceFlags': {
                'legend': True,
                'mdProfile': name in md_profiles,
                'htmlLegacy': name in html_id_by_name,
                'pattern': name in battle_canonical_names,
            },
            'note': note,
            'extraRules': preserved_notes,
        })
    return baseline, id_by_name


canonical_legend_keys = {}
canonical_alias_keys = {}

def canonical_name(raw, legend_name_set, md_aliases, html_alias_map, id_to_name):
    text = str(raw or '').strip()
    if not text:
        return None
    if text in legend_name_set:
        return text
    if text in md_aliases and md_aliases[text] in legend_name_set:
        return md_aliases[text]
    if text in html_alias_map and html_alias_map[text] in id_to_name:
        return id_to_name[html_alias_map[text]]
    key = hero_key(text)
    if key in canonical_legend_keys:
        return canonical_legend_keys[key]
    if key in canonical_alias_keys and canonical_alias_keys[key] in legend_name_set:
        return canonical_alias_keys[key]
    return None


def build_battle_normalization(legend_names, md_aliases, html_alias_map, id_to_name):
    global canonical_legend_keys, canonical_alias_keys
    canonical_legend_keys = {hero_key(name): name for name in legend_names}
    canonical_alias_keys = {hero_key(alias): target for alias, target in md_aliases.items()}
    for alias, target_id in html_alias_map.items():
        target_name = id_to_name.get(target_id)
        if target_name:
            canonical_alias_keys[hero_key(alias)] = target_name
    return set(legend_names)


@dataclass
class NormalizedBattle:
    my_picks: list
    enemy_picks: list
    my_prebans: list
    enemy_prebans: list
    my_ban: str | None
    enemy_ban: str | None
    my_firstpick: bool
    result_win: bool


def parse_battles(legend_names, md_aliases, html_alias_map, id_to_name):
    raw = json.loads(read_text(BATTLE_PATH))
    legend_name_set = build_battle_normalization(legend_names, md_aliases, html_alias_map, id_to_name)
    raw_battle_heroes, raw_mappable = set(), {}
    battles = []

    def map_name(value):
        raw_name = str(value or '').strip()
        if not raw_name:
            return None
        raw_battle_heroes.add(raw_name)
        mapped = canonical_name(raw_name, legend_name_set, md_aliases, html_alias_map, id_to_name)
        if mapped and raw_name not in legend_name_set:
            raw_mappable[raw_name] = mapped
        return mapped

    for account in raw:
        for battle in account.get('battles') or []:
            my_team = battle.get('my_team') or {}
            enemy_team = battle.get('enemy_team') or {}
            my_picks = unique_preserve([mapped for mapped in (map_name(v) for v in my_team.get('pick_codes') or []) if mapped])
            enemy_picks = unique_preserve([mapped for mapped in (map_name(v) for v in enemy_team.get('pick_codes') or []) if mapped])
            if not my_picks or not enemy_picks:
                continue
            battles.append(NormalizedBattle(
                my_picks=my_picks,
                enemy_picks=enemy_picks,
                my_prebans=unique_preserve([mapped for mapped in (map_name(v) for v in my_team.get('preban_codes') or []) if mapped]),
                enemy_prebans=unique_preserve([mapped for mapped in (map_name(v) for v in enemy_team.get('preban_codes') or []) if mapped]),
                my_ban=map_name(my_team.get('ban_code')),
                enemy_ban=map_name(enemy_team.get('ban_code')),
                my_firstpick=bool(battle.get('my_firstpick')),
                result_win=str(battle.get('result') or '').strip() != '패',
            ))
            detail = battle.get('detail') or {}
            for slot in detail.values():
                if isinstance(slot, dict):
                    map_name(slot.get('hero_code'))
    return battles, raw_battle_heroes, raw_mappable


def rate(count, total):
    return count / total if total else 0.0


def reliability(count, pivot):
    return min(1.0, math.log1p(count) / math.log1p(pivot)) if count > 0 else 0.0

def build_patterns(battles, legend_names, md_aliases, html_alias_map, id_to_name):
    raw = json.loads(read_text(BATTLE_PATH))
    legend_name_set = set(legend_names)
    total_battles = len(battles)
    total_teams = total_battles * 2
    hero_presence = Counter(); hero_wins = Counter(); hero_games = Counter()
    hero_prebans = Counter(); hero_firstpicks = Counter(); hero_vanguards = Counter(); hero_bans = Counter()
    pair_counts = Counter(); package_counts = Counter(); weak_encounters = Counter(); weak_bans = Counter()
    hero_set_counts = defaultdict(Counter)
    deferred_pairs, deferred_packages, deferred_weak = [], [], []

    for account in raw:
        for battle in account.get('battles') or []:
            detail = battle.get('detail') or {}
            for slot in detail.values():
                if not isinstance(slot, dict):
                    continue
                hero = canonical_name(slot.get('hero_code'), legend_name_set, md_aliases, html_alias_map, id_to_name)
                if not hero:
                    continue
                for set_code in slot.get('set_codes') or []:
                    hero_set_counts[hero][str(set_code).strip()] += 1

    for battle in battles:
        my_win, enemy_win = battle.result_win, (not battle.result_win)
        for hero in battle.my_picks:
            hero_presence[hero] += 1; hero_games[hero] += 1
            if my_win: hero_wins[hero] += 1
        for hero in battle.enemy_picks:
            hero_presence[hero] += 1; hero_games[hero] += 1
            if enemy_win: hero_wins[hero] += 1
        for hero in battle.my_prebans + battle.enemy_prebans:
            hero_prebans[hero] += 1
        if battle.my_firstpick and battle.my_picks:
            hero_firstpicks[battle.my_picks[0]] += 1
        elif (not battle.my_firstpick) and battle.enemy_picks:
            hero_firstpicks[battle.enemy_picks[0]] += 1
        if len(battle.my_picks) >= 3: hero_vanguards[battle.my_picks[2]] += 1
        if len(battle.enemy_picks) >= 3: hero_vanguards[battle.enemy_picks[2]] += 1
        if battle.my_ban: hero_bans[battle.my_ban] += 1
        if battle.enemy_ban: hero_bans[battle.enemy_ban] += 1
        for team in (battle.my_picks, battle.enemy_picks):
            lineup = sorted(set(team))
            for pair in combinations(lineup, 2): pair_counts[pair] += 1
            for package in combinations(lineup, 3): package_counts[tuple(sorted(package))] += 1
        for my_hero in battle.my_picks:
            for opp in battle.enemy_picks:
                weak_encounters[(my_hero, opp)] += 1
                weak_encounters[(opp, my_hero)] += 1
                if battle.my_ban == my_hero: weak_bans[(my_hero, opp)] += 1
                if battle.enemy_ban == opp: weak_bans[(opp, my_hero)] += 1

    hero_presence_stats = {}; hero_win_stats = {}; hero_preban_stats = {}; hero_firstpick_stats = {}
    hero_vanguard_stats = {}; hero_ban_pressure_stats = {}; hero_set_stats = {}
    for hero in legend_names:
        presence = hero_presence[hero]; games = hero_games[hero]; wins = hero_wins[hero]
        preban = hero_prebans[hero]; firstpick = hero_firstpicks[hero]; vanguard = hero_vanguards[hero]; bans = hero_bans[hero]
        presence_rate = rate(presence, total_teams); win_rate = rate(wins, games)
        preban_rate = rate(preban, total_teams * 2); firstpick_rate = rate(firstpick, total_battles)
        vanguard_rate = rate(vanguard, total_teams); ban_rate_when_picked = rate(bans, games)
        hero_presence_stats[hero] = {'total': presence, 'presenceRate': round(presence_rate, 4)}
        hero_win_stats[hero] = {'games': games, 'winRate': round(win_rate, 4), 'reliabilityScore': round((win_rate - 0.5) * 2 * reliability(games, 80), 4)}
        hero_preban_stats[hero] = {'games': total_battles, 'prebanCount': preban, 'prebanRate': round(preban_rate, 4), 'pressureScore': round(preban_rate * reliability(preban, 40), 4)}
        hero_firstpick_stats[hero] = {'games': total_battles, 'firstPickCount': firstpick, 'firstPickRate': round(firstpick_rate, 4), 'openerScore': round(firstpick_rate * reliability(firstpick, 24), 4)}
        hero_vanguard_stats[hero] = {'games': total_teams, 'vanguardCount': vanguard, 'vanguardRate': round(vanguard_rate, 4), 'protectedCoreScore': round(vanguard_rate * reliability(vanguard, 32), 4)}
        hero_ban_pressure_stats[hero] = {'games': games, 'banCount': bans, 'banRate': round(ban_rate_when_picked, 4), 'banPressureScore': round(max(0.0, ban_rate_when_picked - 0.08) * reliability(bans, 28), 4)}
        if hero_set_counts[hero]:
            total_sets = sum(hero_set_counts[hero].values())
            hero_set_stats[hero] = {set_code: {'count': count, 'rate': round(count / total_sets, 4)} for set_code, count in hero_set_counts[hero].most_common(4)}

    pair_stats = {}
    for (a, b), games in pair_counts.items():
        expected = rate(hero_presence[a], total_teams) * rate(hero_presence[b], total_teams) * total_teams
        observed = rate(games, total_teams); expected_rate = rate(expected, total_teams)
        lift = (observed / expected_rate - 1) if expected_rate > 0 else 0.0
        rel = reliability(games, 20)
        if games >= 4 and expected >= 1.5 and lift >= 0.12:
            pair_stats[f'{a}|{b}'] = {'games': games, 'observedRate': round(observed, 4), 'expectedRate': round(expected_rate, 4), 'lift': round(lift * rel, 4)}
        else:
            deferred_pairs.append((games, lift, a, b))

    package_stats = {}
    for members, games in package_counts.items():
        a, b, c = members
        expected = rate(hero_presence[a], total_teams) * rate(hero_presence[b], total_teams) * rate(hero_presence[c], total_teams) * total_teams
        observed = rate(games, total_teams); expected_rate = rate(expected, total_teams)
        lift = (observed / expected_rate - 1) if expected_rate > 0 else 0.0
        rel = reliability(games, 12)
        if games >= 3 and expected >= 0.8 and lift >= 0.18:
            package_stats['|'.join(members)] = {'games': games, 'observedRate': round(observed, 4), 'expectedRate': round(expected_rate, 4), 'lift': round(lift * rel, 4)}
        else:
            deferred_packages.append((games, lift, members))

    weak_stats = {}
    for (hero, opp), games in weak_encounters.items():
        hint_rate = rate(weak_bans[(hero, opp)], games)
        baseline_ban = rate(hero_bans[hero], hero_games[hero])
        hint_score = max(0.0, hint_rate - baseline_ban)
        rel = reliability(games, 18)
        if games >= 5 and hint_score >= 0.10:
            weak_stats[f'{hero}|{opp}'] = {'games': games, 'banRateVsOpponent': round(hint_rate, 4), 'baselineBanRate': round(baseline_ban, 4), 'hintScore': round(hint_score * rel, 4)}
        else:
            deferred_weak.append((games, hint_score, hero, opp))

    compiled = {
        'version': 2,
        'contract': {'layer': 'pattern only', 'allowed': ['preban_rate', 'firstpick_rate', 'vanguard_rate', 'pair/package lift', 'ban pressure', 'weak matchup hint'], 'forbidden': ['meta overwrite', 'hard/syn overwrite', 'ban_code => hard counter']},
        'source': {'battle_accounts_merged': str(BATTLE_PATH.relative_to(ROOT)).replace('\\', '/'), 'totalBattles': total_battles, 'totalTeams': total_teams},
        'normalization': {'metaSource': 'legend only', 'patternSource': 'battle_accounts_merged only', 'prebanUsage': 'ui/ranking + urgency layer only', 'firstpickUsage': 'early layer only', 'vanguardUsage': 'third-pick layer only', 'banCodeUsage': 'ban pressure + weak hint only'},
        'heroPresenceStats': hero_presence_stats,
        'heroWinStats': hero_win_stats,
        'heroPrebanStats': hero_preban_stats,
        'heroFirstPickStats': hero_firstpick_stats,
        'heroVanguardStats': hero_vanguard_stats,
        'heroBanPressureStats': hero_ban_pressure_stats,
        'heroPairStats': pair_stats,
        'heroPackageStats': package_stats,
        'heroSetStats': hero_set_stats,
        'weakMatchupHintStats': weak_stats,
    }
    deferred = {'pairs': sorted(deferred_pairs, reverse=True)[:40], 'packages': sorted(deferred_packages, reverse=True)[:40], 'weak': sorted(deferred_weak, reverse=True)[:40]}
    return compiled, deferred

def js_dumps(value, indent=2):
    return json.dumps(value, ensure_ascii=False, indent=indent)


def build_hero_block(baseline_heroes, alias_map):
    heroes_js = js_dumps(baseline_heroes, indent=2).replace('"', "'")
    alias_js = ', '.join(f"{json.dumps(k, ensure_ascii=False)}:{json.dumps(v, ensure_ascii=False)}" for k, v in alias_map.items()).replace('"', "'")
    return f"const HEROES = {heroes_js};\n\nconst HERO_BY_ID = Object.fromEntries(HEROES.map(h => [h.id, h]));\nconst HERO_ID_ALIASES = {{{alias_js}}};\nconst ALIAS_HERO_IDS = new Set(Object.keys(HERO_ID_ALIASES));\nconst KNOWN_KOREAN_HERO_NAMES = new Set(HEROES.map(h => h.name));\n"


def snapshot_helper_block():
    return """function snapshotHeroManualLegendBase(hero){
  if(!hero) return null;
  if(!hero.__manualLegendBase){
    hero.__manualLegendBase = {
      pick: Number(hero.pick || 0),
      win: Number(hero.win || 0),
      ban: Number(hero.ban || 0),
      hard: [...normalizeHeroIdList(hero.hard)],
      syn: [...normalizeHeroIdList(hero.syn)],
      sets: [...normalizeHeroSetList(hero.sets)]
    };
  }
  return hero.__manualLegendBase;
}
function restoreHeroManualLegendBase(hero){
  const base = hero?.__manualLegendBase;
  if(!hero || !base) return false;
  hero.pick = Number(base.pick || 0);
  hero.win = Number(base.win || 0);
  hero.ban = Number(base.ban || 0);
  hero.hard = [...normalizeHeroIdList(base.hard)];
  hero.syn = [...normalizeHeroIdList(base.syn)];
  hero.sets = [...normalizeHeroSetList(base.sets)];
  delete hero.__legendAppliedKey;
  return true;
}
function restoreAllHeroManualLegendBases(){
  for(const hero of HEROES) restoreHeroManualLegendBase(hero);
}
function captureAllHeroManualLegendBases(){
  for(const hero of HEROES) snapshotHeroManualLegendBase(hero);
}
"""


def patch_html(html_text, baseline_heroes, alias_map, compiled_patterns):
    html_text = re.sub(r"const HEROES = \[.*?function heroKey\(value\)\{", build_hero_block(baseline_heroes, alias_map) + 'function heroKey(value){', html_text, count=1, flags=re.S)
    html_text = re.sub(r"\nconst HERO_LEGEND_BASELINE_SETS = Object\.freeze\(.*?\nfunction resetHeroLegendCaches\(\)\{", "\nfunction resetHeroLegendCaches(){", html_text, count=1, flags=re.S)
    html_text = re.sub(r"function applyBuiltInHeroLegendSetBaselines\(\)\{.*?function getHeroExplanationData\(heroValue\)\{", snapshot_helper_block() + 'function getHeroExplanationData(heroValue){', html_text, count=1, flags=re.S)
    embedded_block = "const EMBEDDED_COMPILED_PATTERN_DATA = " + js_dumps(compiled_patterns, indent=2) + ";\n"
    html_text = re.sub(r"const compiledPatternState = \{.*?\};\n", lambda m: m.group(0) + embedded_block, html_text, count=1, flags=re.S)
    hero_info = """function updateHeroLegendInfo(){
  const el = document.getElementById('heroLegendInfo');
  if(!el) return;
  if(!heroLegendState.ready){
    const base = `embedded baseline hero ${HEROES.length}명 적용`;
    el.textContent = heroLegendState.lastError ? `${base} · ${heroLegendState.lastError}` : `${base} · hero_full_legend 업로드 시 override`;
    return;
  }
  const base = `hero_full_legend ${heroLegendState.loadedEntries}명 적용`;
  el.textContent = heroLegendState.sourceLabel ? `${base} · ${heroLegendState.sourceLabel}` : base;
}
"""
    html_text = re.sub(r"function updateHeroLegendInfo\(\)\{.*?\n\}\nasync function loadHeroLegendData", hero_info + 'async function loadHeroLegendData', html_text, count=1, flags=re.S)
    compiled_loader = """function applyCompiledPatternData(raw, sourceLabel='embedded compiled_patterns'){
  compiledPatternState.raw = raw;
  compiledPatternState.compiled = preprocessCompiledPatternData(raw);
  compiledPatternState.ready = true;
  compiledPatternState.sourceLabel = sourceLabel;
  compiledPatternState.lastError = '';
  compiledPatternState.loadedEntries = compiledPatternState.compiled.heroPresenceStats.size;
  invalidateBattlecollectCache();
  updateBattlecollectInfo();
  render('board');
}
async function loadCompiledPatternData(forceReload=false, file=null){
  try{
    const raw = file ? JSON.parse(await file.text()) : EMBEDDED_COMPILED_PATTERN_DATA;
    const sourceLabel = file?.name || 'embedded compiled_patterns';
    applyCompiledPatternData(raw, sourceLabel);
    if(file) appendRuntimeLog(`compiled pattern 로드: ${sourceLabel}`);
  }catch(err){
    compiledPatternState.ready = false;
    compiledPatternState.compiled = null;
    compiledPatternState.lastError = `compiled_patterns 로드 실패: ${String(err?.message || err || '알 수 없는 오류')}`;
    updateBattlecollectInfo();
  }
}
"""
    html_text = re.sub(r"async function loadCompiledPatternData\(forceReload=false, file=null\)\{.*?\n\}\nfunction appendRuntimeLog", compiled_loader + 'function appendRuntimeLog', html_text, count=1, flags=re.S)
    clear_battle = """function clearBattlecollectData(){
  applyCompiledPatternData(EMBEDDED_COMPILED_PATTERN_DATA, 'embedded compiled_patterns');
  const input = document.getElementById('battlecollectFilesInput');
  if(input) input.value='';
  appendRuntimeLog('compiled_patterns baseline 복원');
}
"""
    html_text = re.sub(r"function clearBattlecollectData\(\)\{.*?\n\}\nasync function handleBattlecollectFiles", clear_battle + 'async function handleBattlecollectFiles', html_text, count=1, flags=re.S)
    handle_battle = """async function handleBattlecollectFiles(fileList){
  const files = Array.from(fileList || []).filter(file => /\\.json$/i.test(file.name));
  if(!files.length){
    clearBattlecollectData();
    return;
  }
  await loadCompiledPatternData(true, files[0]);
}
"""
    html_text = re.sub(r"async function handleBattlecollectFiles\(fileList\)\{.*?\n\}\nfunction updateHeroLegendInfo", handle_battle + 'function updateHeroLegendInfo', html_text, count=1, flags=re.S)
    html_text = html_text.replace("applyBuiltInHeroLegendSetBaselines();\ncaptureAllHeroManualLegendBases();", "captureAllHeroManualLegendBases();")
    html_text = html_text.replace("loadCompiledPatternData().catch(() => {});\nloadHeroLegendData(false, null, true).catch(() => {});", "applyCompiledPatternData(EMBEDDED_COMPILED_PATTERN_DATA, 'embedded compiled_patterns');")
    return html_text

def patch_md(md_text, legend_entries, baseline_heroes, md_profiles):
    header = """# Epic Seven Hero Rules / Draft Rules
기준일: 2026-03-17
출처 우선순위:
1. `hero_rules22.md`의 규칙/별칭/특수 태그/사용자 메모
2. `hero_full_legend.json`의 baseline stats / hard / syn / top sets
3. `battle_accounts_merged.json`에서 컴파일한 pattern layer (preban / firstpick / vanguard / pair / package / ban pressure / weak hint)

주의:
- **규칙/별칭/특수 메모의 source of truth는 이 문서**로 유지한다.
- **픽률/승률/밴률, hard/syn, 장비 세트의 source of truth는 hero_full_legend baseline**으로 본다.
- **battle 패턴은 meta를 덮어쓰지 않고**, early/urgency/vanguard/pair/package/weak hint 보정층으로만 사용한다.
"""
    md_text = re.sub(r'^# Epic Seven Hero Rules / Draft Rules\n.*?\n## 1\)', header + '\n## 1)', md_text, count=1, flags=re.S)
    baseline_lookup = {hero['name']: hero for hero in baseline_heroes}
    lines = ['## 6) 통합 영웅 프로필', '- 아래 프로필은 **hero_rules22의 규칙/메모 + hero_full_legend baseline 수치/관계/세트**를 합쳐 다시 정리했다.', '- 픽률/승률/밴률, hard/syn, 세트는 legend 최신값으로 통일했다.', '- 기존 사용자 텍스트 규칙/특수 메모/하르세티/선턴/속도 관련 메모는 별도 보존했다.', '']
    for entry in legend_entries:
        name = entry['name']; hero = baseline_lookup[name]; notes = extract_preserved_notes(md_profiles.get(name, ''))
        lines += [f'### {name}', '- 최신 baseline 수치:', f"  - 픽률: {entry['pick']:.2f}%", f"  - 승률: {entry['win']:.2f}%", f"  - 밴률: {entry['ban']:.2f}%", '- legend baseline 관계:', f"  - hard: {', '.join(entry['hard_names'][:4]) if entry['hard_names'] else '없음'}", f"  - syn: {', '.join(entry['syn_names'][:4]) if entry['syn_names'] else '없음'}", f"  - sets: {', '.join(entry['sets'][:2]) if entry['sets'] else '없음'}", '- source flags:', '  - legend: true', f"  - mdProfile: {'true' if hero['sourceFlags']['mdProfile'] else 'false'}", f"  - htmlLegacy: {'true' if hero['sourceFlags']['htmlLegacy'] else 'false'}", f"  - pattern: {'true' if hero['sourceFlags']['pattern'] else 'false'}"]
        if notes:
            lines.append('- 기존 사용자 규칙/메모:')
            lines.extend([f'  - {note}' for note in notes])
        else:
            lines.append('- 기존 사용자 규칙/메모: 없음')
        lines.append('')
    new_section = '\n'.join(lines).rstrip() + '\n'
    return re.sub(r'## 6\) 통합 영웅 프로필\n.*?(?:\n## |\Z)', new_section + '\n', md_text, count=1, flags=re.S)


def build_diff_report(summary, deferred):
    lines = [
        '# Compiled Diff Report',
        '',
        f"- legend hero count: {summary['legend_count']}",
        f"- HTML baseline hero count: {summary['html_baseline_count']}",
        f"- MD profile count: {summary['md_profile_count']}",
        f"- unresolved alias/raw hero string count: {len(summary['battle_only_raw'])}",
        '- runtime unknown hero creation disabled: yes',
        '',
        '## Newly Added Heroes'
    ]
    lines.extend([f'- {name}' for name in summary['legend_not_html']])
    lines += ['', '## Legend Heroes Missing In MD Profiles']
    lines.extend([f'- {name}' for name in summary['legend_not_md']])
    lines += ['', '## Unresolved Alias Or Raw Hero Strings']
    lines.extend([f'- {name}' for name in summary['battle_only_raw']])
    lines += ['', '## Battle Strings Normalized To Legend']
    lines.extend([f"- {raw} -> {canon}" for raw, canon in summary['battle_alias_mapped']])
    lines += ['', '## Total Score Canonical Buckets', '- meta', '- synergy', '- counters', '- pack', '- completion', '- early', '- urgency', '- exposure', '- relief', '- vanguard', '- archetype', '- reproducibility', '- openCounter']
    lines += ['', '## Pattern To Bucket Attribution', '- contextualReliability -> reproducibility', '- speedContest -> archetype', '- firstTurnPenalty -> reproducibility', '- logTurnBonus -> early or vanguard', '- repeatAxisPenalty -> pack/completion damping', '- pair lift -> synergy', '- package lift -> pack']
    lines += ['', '## Deferred Pair Patterns']
    lines.extend([f"- games={g}, lift={lift:.3f}, pair={a} | {b}" for g, lift, a, b in deferred['pairs'][:20]])
    lines += ['', '## Deferred Package Patterns']
    lines.extend([f"- games={g}, lift={lift:.3f}, package={' | '.join(members)}" for g, lift, members in deferred['packages'][:20]])
    lines += ['', '## Deferred Weak Hints']
    lines.extend([f"- games={g}, hint={hint:.3f}, matchup={hero} -> {opp}" for g, hint, hero, opp in deferred['weak'][:20]])
    return '\n'.join(lines) + '\n'


def main():
    html_text = read_text(HTML_PATH); md_text = read_text(MD_PATH)
    legend_entries = parse_legend(); legend_names = [entry['name'] for entry in legend_entries]
    html_heroes, html_alias_map = parse_html_heroes(html_text)
    html_id_by_name = {hero['name']: hero['id'] for hero in html_heroes}
    html_notes = {hero['name']: hero['note'] for hero in html_heroes if hero['note']}
    md_aliases = parse_md_aliases(md_text)
    md_profile_count, md_profiles, _ = parse_md_profiles(md_text)
    initial_id_to_name = {hero['id']: hero['name'] for hero in html_heroes}
    battles, raw_battle_heroes, raw_mappable = parse_battles(legend_names, md_aliases, html_alias_map, initial_id_to_name)
    battle_canonical_names = set(raw_mappable.values()) | {name for battle in battles for name in (battle.my_picks + battle.enemy_picks)}
    baseline_heroes, id_by_name = build_baseline(legend_entries, html_notes, md_profiles, html_id_by_name, battle_canonical_names)
    id_to_name = {hero['id']: hero['name'] for hero in baseline_heroes}
    alias_map = dict(html_alias_map)
    for alias, target_name in md_aliases.items():
        if target_name in id_by_name: alias_map[alias] = id_by_name[target_name]
    compiled_patterns, deferred = build_patterns(battles, legend_names, md_aliases, html_alias_map, id_to_name)
    summary = {'legend_count': len(legend_entries), 'html_explicit_before': len(html_heroes), 'html_baseline_count': len(baseline_heroes), 'md_profile_count': md_profile_count, 'legend_not_html': sorted([name for name in legend_names if name not in html_id_by_name]), 'legend_not_md': sorted([name for name in legend_names if name not in md_profiles]), 'battle_only_raw': sorted([name for name in raw_battle_heroes if name not in set(legend_names)]), 'battle_alias_mapped': sorted(raw_mappable.items())}
    COMPILED_HEROES_PATH.write_text(json.dumps({'version': 1, 'source': {'legend': str(LEGEND_PATH.relative_to(ROOT)).replace('\\', '/'), 'md': str(MD_PATH.relative_to(ROOT)).replace('\\', '/')}, 'heroes': baseline_heroes, 'aliases': alias_map, 'counts': {'legend': len(legend_entries), 'html_explicit_before': len(html_heroes), 'md_profiles_before': md_profile_count, 'baseline': len(baseline_heroes)}}, ensure_ascii=False, indent=2), encoding='utf-8')
    COMPILED_PATTERNS_PATH.write_text(json.dumps(compiled_patterns, ensure_ascii=False, indent=2), encoding='utf-8')
    COMPILED_DIFF_PATH.write_text(build_diff_report(summary, deferred), encoding='utf-8')
    write_text(HTML_PATH, patch_html(html_text, baseline_heroes, alias_map, compiled_patterns))
    write_text(MD_PATH, patch_md(md_text, legend_entries, baseline_heroes, md_profiles))
    print(json.dumps({'legend_count': len(legend_entries), 'html_explicit_before': len(html_heroes), 'html_baseline_after': len(baseline_heroes), 'md_profiles_before': md_profile_count, 'new_html_heroes': len(summary['legend_not_html']), 'new_md_profiles': len(summary['legend_not_md']), 'battle_only_raw_count': len(summary['battle_only_raw']), 'battle_alias_mapped_count': len(summary['battle_alias_mapped'])}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()



