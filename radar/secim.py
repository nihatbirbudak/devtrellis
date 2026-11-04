"""Günlük adayları konu çeşitliliği, proje tekrarı ve bekleme süresiyle açıklanabilir biçimde seçer."""

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from radar.depo import tarih
from radar.konular import KONULAR


def _kaynak_tarihi(item, now):
    """Eşitlik için geçerli kaynak zamanını seçer; güncellemeyi yayın tarihi yapmaz."""
    for field, kind in (('published_at', 'publication'), ('updated_at', 'source_updated')):
        value = item.get(field)
        if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            try:
                parsed = datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        else:
            parsed = tarih(value)
        if parsed is not None and parsed <= now:
            return value, kind, parsed.timestamp()
    return None, None, float('-inf')


def proje_anahtari(item, sources=None):
    """Açık depo kimliğini kullanır; yalnız aynı yayıncıdan gelmeyi aynı proje saymaz."""
    parts = urlsplit(item.get('url') or '')
    path = [part for part in parts.path.split('/') if part]
    if parts.hostname in {'github.com', 'www.github.com'} and len(path) >= 2:
        return 'github:' + '/'.join(path[:2]).casefold()
    source = (sources or {}).get(item.get('source_id'), {})
    if source.get('kind') == 'github':
        return 'release-source:' + item['source_id']
    # Başlıkta açık sürüm numarası varsa aynı adın ardışık sürümleri bir gruptur.
    title = item.get('source_title') or item.get('title', '')
    if re.search(r'\bv?\d+\.\d+(?:\.\d+)?\b', title, re.I):
        name = re.sub(r'\bv?\d+\.\d+(?:\.\d+)?(?:[-+.][\w.]+)?\b', ' ', title, flags=re.I)
        name = re.sub(r'\b(?:release|released|version|sürüm|sürümü)\b', ' ', name, flags=re.I)
        name = ' '.join(re.findall(r'\w+', name.casefold()))
        if len(name) >= 3:
            return 'version:' + str(parts.hostname) + ':' + name
    return 'item:' + item['id']


def gunluk_sec(items, *, limit, now, sources=None, last_topics=None, exploration_percent=0):
    """Her turda az temsil edilen konuyu seçer; proje başına günde en çok bir kayıt verir.

    Beklenen her gün iki kural puanı ekler. Bu puan ve model uygunluk puanı
    olasılık ya da doğruluk yüzdesi değildir. Tarih yoksa bekleme süresi uydurulmaz.
    """
    if type(limit) is not int or not 1 <= limit <= 24:
        raise ValueError('Günlük seçim sınırı geçersiz.')
    if type(exploration_percent) is not int or not 0 <= exploration_percent <= 50:
        raise ValueError('Yeni kaynak deneme payı geçersiz.')
    trial_limit = max(1, limit * exploration_percent // 100) if exploration_percent else 0
    trial_selected = 0
    groups = {topic: [] for topic in KONULAR}
    date_order = {}
    for item in items:
        rank = item.get('personal_rank') or {}
        if (item.get('topic') not in groups or item.get('feedback') == 'gereksiz'
                or rank.get('topic_weight', 3) == 0 or rank.get('muted')
                or item.get('personal_score', 0) < 0):
            continue
        first_seen = tarih(item.get('first_seen_at'))
        age = max(0, (now - first_seen).days) if first_seen else 0
        score = item.get('personal_score', item.get('score', 0))
        record = {'item_id': item['id'], 'topic': item['topic'], 'project_key': proje_anahtari(item, sources),
                  'personal_score': score, 'waiting_days': age, 'age_bonus': 2 * age,
                  'selection_score': score + 2 * age, 'source_revision': item['revision'],
                  'method': 'topic_round_robin_project_cap_age',
                  'reason': 'Konu sırası, kişisel ilgi ve bekleme süresi; aynı proje bugün bir kez seçilir.'}
        record['source_trial'] = (sources or {}).get(item.get('source_id'), {}).get('learning_lifecycle') == 'trial'
        source_date, date_kind, date_rank = _kaynak_tarihi(item, now)
        record['selection_date'], record['selection_date_kind'] = source_date, date_kind
        date_order[item['id']] = date_rank
        groups[item['topic']].append(record)
    for entries in groups.values():
        entries.sort(key=lambda row: (-row['selection_score'], -row['waiting_days'],
                                      -date_order[row['item_id']], row['item_id']))
    counts, used, selected = dict.fromkeys(KONULAR, 0), set(), []
    last_topics = last_topics or {}
    while len(selected) < limit:
        for topic in groups:
            groups[topic] = [row for row in groups[topic] if row['project_key'] not in used
                             and not (trial_limit and trial_selected >= trial_limit and row['source_trial'])]
            # Konuların sırası korunur; o konudaki sınırlı deneme payı önce kullanılır.
            if trial_limit and trial_selected < trial_limit:
                groups[topic].sort(key=lambda row: (not row['source_trial'], -row['selection_score'],
                                                     -row['waiting_days'], -date_order[row['item_id']], row['item_id']))
        available = [topic for topic in KONULAR if groups[topic]]
        if not available:
            break
        topic = min(available, key=lambda value: (counts[value], last_topics.get(value, ''),
                                                 -groups[value][0]['selection_score'], value))
        row = groups[topic].pop(0)
        row['position'] = len(selected) + 1
        row['topic_position'] = counts[topic] + 1
        if trial_limit and row['source_trial']:
            trial_selected += 1
            row['method'] = 'topic_round_robin_project_cap_age_source_trial'
            row['reason'] = 'Yeni kaynak için sınırlı deneme payı; konu sırası ve proje çeşitliliği korunur.'
        row['source_trial_limit'] = trial_limit
        if row['selection_date_kind']:
            label = 'yayın tarihi' if row['selection_date_kind'] == 'publication' else 'kaynak güncelleme tarihi'
            row['reason'] += f' Eşit puan ve beklemede {label} kullanılır.'
        selected.append(row)
        counts[topic] += 1
        used.add(row['project_key'])
    return selected
