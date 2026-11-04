"""Kaynak katkısını ölçer; yeterli açık insan kanıtıyla sıklığı gerekçeli ve geri alınabilir değiştirir."""

import json
import math
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from radar.depo import tarih


TEMEL_KAYNAKLAR = {'home_assistant', 'mosquitto', 'claude_agent_sdk', 'pipecat'}
KANIT_ACIKLAMASI = ('Katkı asıl haber kaynağına yazılır. Okunmayan, bekleyen veya başarısız işler olumsuz oy değildir. '
                    'Sıklık kararı ölçüm başlangıcından sonraki en fazla 90 günlük kanıtı kullanır. '
                    'Toplama maliyeti yalnız ölçüm başladıktan sonraki turları kapsar; para ve token maliyeti bilinmiyor.')


class KaynakOgrenmeCatismasi(ValueError):
    """Eski pencerenin yeni kaynak kararını değiştirmesini engeller."""


def _surum(value):
    if type(value) is not int or value < 0:
        raise ValueError('Kaynak öğrenme sürümü geçersiz.')


def _tablolar(connection):
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def etkin_araliklar(connection):
    """Kullanıcı tercihleri üzerine uygulanabilecek etkin politika kayıtlarını verir."""
    if 'source_learning_settings' not in _tablolar(connection):
        return {}
    settings = connection.execute('SELECT enabled FROM source_learning_settings WHERE id=1').fetchone()
    if settings is None:
        return {}
    return {row['source_id']: {**dict(row), 'learning_enabled': bool(settings['enabled'])}
            for row in connection.execute('SELECT * FROM source_learning_state')}


def araligi_uygula(source, preference, learning):
    """Elle seçilen sıklığı ve kapalı kaynağı koruyarak politika aralığını görünüşe ekler."""
    configured = source.get('configured_interval_hours', source['interval_hours'])
    source['configured_interval_hours'] = configured
    source['learning_lifecycle'] = learning.get('lifecycle', 'regular')
    source['learning_protected'] = bool(learning.get('protected', False))
    source['learning_version'] = learning.get('version', 0)
    source['learning_enabled'] = bool(learning.get('learning_enabled', False))
    active = (learning.get('learning_enabled') and not learning.get('protected')
              and source.get('enabled', True) and preference.get('interval_hours') is None
              and learning.get('interval_hours') is not None
              and learning.get('base_interval_hours') == configured)
    source['interval_hours'] = learning['interval_hours'] if active else configured
    source['learning_interval_active'] = bool(active)
    # Tarih yalnız bir politika aralığı bulunduğunda yeniden hesaplanır. Normal kaynakların
    # mevcut vadesi, elle yeniden deneme hakkı ve testlerdeki geçici vade korunur.
    if learning.get('interval_hours') is not None and (checked := tarih(source.get('last_checked_at'))):
        backoff = min(168, 2 ** min(int(source.get('failures', 0)), 7)) if source.get('last_error') else 0
        source['next_due_at'] = (checked + timedelta(hours=max(source['interval_hours'], backoff))).isoformat()
    return source


class KaynakOgrenme:
    def __init__(self, depo, clock=None):
        self.depo = depo
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        with depo.baglanti(write=True) as connection:
            for statement in (
                '''CREATE TABLE IF NOT EXISTS source_learning_settings (
                    id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 1,
                    exploration_percent INTEGER NOT NULL DEFAULT 20, version INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL, last_evaluated_at TEXT)''',
                '''CREATE TABLE IF NOT EXISTS source_learning_state (
                    source_id TEXT PRIMARY KEY REFERENCES sources(id), protected INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 0, lifecycle TEXT NOT NULL DEFAULT 'regular',
                    interval_hours REAL, base_interval_hours REAL, observed_since TEXT NOT NULL,
                    trial_started_at TEXT, last_policy_at TEXT, hold_until TEXT,
                    provenance_json TEXT NOT NULL DEFAULT '{}')''',
                '''CREATE TABLE IF NOT EXISTS source_collection_runs (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id), started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL, status TEXT NOT NULL, returned INTEGER, added INTEGER,
                    changed INTEGER, duplicates INTEGER, duration_seconds REAL, pages INTEGER,
                    cost_amount REAL, tokens INTEGER, error TEXT)''',
                'CREATE INDEX IF NOT EXISTS collection_runs_date ON source_collection_runs(started_at,source_id)',
                '''CREATE TABLE IF NOT EXISTS source_learning_changes (
                    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
                    before_interval REAL NOT NULL, after_interval REAL NOT NULL,
                    before_lifecycle TEXT NOT NULL, after_lifecycle TEXT NOT NULL,
                    before_overlay REAL, before_base REAL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
                    reverted INTEGER NOT NULL DEFAULT 0, reverted_at TEXT, version INTEGER NOT NULL DEFAULT 0,
                    state_version INTEGER NOT NULL)''',
                '''CREATE TABLE IF NOT EXISTS source_learning_snapshots (
                    day TEXT PRIMARY KEY, created_at TEXT NOT NULL, report_json TEXT NOT NULL)''',
            ):
                connection.execute(statement)
            connection.execute('INSERT OR IGNORE INTO source_learning_settings(id,started_at) VALUES(1,?)',
                               (self.clock().isoformat(),))
            self._kaynaklari_hazirla(connection)

    def _kaynaklari_hazirla(self, connection):
        now = self.clock().isoformat()
        for row in connection.execute('SELECT id,config FROM sources'):
            config = json.loads(row['config'])
            protected = row['id'] in TEMEL_KAYNAKLAR or config.get('kind') == 'discovery'
            connection.execute('INSERT OR IGNORE INTO source_learning_state(source_id,protected,observed_since) VALUES(?,?,?)',
                               (row['id'], int(protected), now))

    def tarama_kaydet(self, source_id, *, started_at, finished_at, status, returned=None,
                     added=None, changed=None, duplicates=None, duration_seconds=None, pages=None, error=None):
        """Gerçek bir toplama turunu bir kez ekler; bilinmeyen parasal maliyeti uydurmaz."""
        if status not in {'ok', 'not_modified', 'partial', 'error'}:
            raise ValueError('Tarama ölçüm durumu geçersiz.')
        if tarih(started_at) is None or tarih(finished_at) is None:
            raise ValueError('Tarama ölçüm zamanı geçersiz.')
        for value in (returned, added, changed, duplicates, pages):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError('Tarama sayımı negatif olmayan tam sayı olmalı.')
        if duration_seconds is not None and (isinstance(duration_seconds, bool)
                or not isinstance(duration_seconds, (int, float)) or not math.isfinite(duration_seconds) or duration_seconds < 0):
            raise ValueError('Tarama süresi geçersiz.')
        with self.depo.baglanti(write=True) as connection:
            self._kaynaklari_hazirla(connection)
            connection.execute('INSERT INTO source_collection_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                               (uuid.uuid4().hex, source_id, started_at, finished_at, status, returned,
                                added, changed, duplicates, duration_seconds, pages, None, None,
                                str(error)[:600] if error else None))

    def _olcumler(self, connection, sources, states):
        now = self.clock()
        restorations = {row['source_id']: tarih(row['restored_at']) for row in connection.execute(
            "SELECT source_id,MAX(created_at) AS restored_at FROM source_learning_changes "
            "WHERE before_lifecycle='slow' AND after_lifecycle='regular' AND reverted=0 GROUP BY source_id")}
        metrics = {source['id']: {
            'collected': 0, 'examined': 0, 'human_positive': 0, 'human_negative': 0, 'developed': 0,
            'observed_examined': 0, 'observed_human_positive': 0, 'observed_human_negative': 0,
            'observed_developed': 0, 'model_calls': 0, 'reads': 0, 'human_read': 0,
            'post_change_examined': 0, 'post_change_human_positive': 0,
            'post_change_human_negative': 0, 'post_change_developed': 0,
            'model_high_fit': 0, 'collection_runs': 0, 'errors': 0, 'duplicates': None,
            'returned': None, 'added': None, 'changed': None, 'duration_seconds': None,
            'cost_amount': None, 'tokens': None,
            'observation_days': max(0, (now - tarih(states[source['id']]['observed_since'])).days),
            'observed_since': states[source['id']]['observed_since'],
            'policy_since': max(tarih(states[source['id']]['observed_since']), now - timedelta(days=90),
                                restorations.get(source['id']) or tarih(states[source['id']]['observed_since'])).isoformat(),
            'policy_window_days': 90,
        } for source in sources}
        for identifier, values in metrics.items():
            values['post_change_since'] = max(tarih(values['policy_since']),
                tarih(states[identifier].get('last_policy_at')) or tarih(values['policy_since'])).isoformat()
        tables = _tablolar(connection)
        for row in connection.execute('SELECT source_id,COUNT(DISTINCT item_id) AS amount FROM source_entries GROUP BY source_id'):
            metrics[row['source_id']]['collected'] = row['amount']
        for row in connection.execute('''SELECT i.source_id,f.value,f.created_at FROM feedback_events f
                JOIN items i ON i.id=f.item_id WHERE f.actor='yerel_kullanici' AND f.id=(
                SELECT MAX(latest.id) FROM feedback_events latest WHERE latest.item_id=f.item_id
                AND latest.actor='yerel_kullanici')'''):
            field = {'yararli': 'human_positive', 'gereksiz': 'human_negative'}.get(row['value'])
            if field:
                values = metrics[row['source_id']]
                values[field] += 1
                if row['created_at'] >= values['policy_since']:
                    values['observed_' + field] += 1
                if row['created_at'] > values['post_change_since']:
                    values['post_change_' + field] += 1
        for table, field in (('ai_attempts', 'model_calls'), ('article_read_attempts', 'reads')):
            if table in tables:
                for row in connection.execute('SELECT i.source_id,COUNT(*) AS amount FROM ' + table +
                        ' a JOIN items i ON i.id=a.item_id GROUP BY i.source_id'):
                    metrics[row['source_id']][field] = row['amount']
        if 'item_read_states' in tables:
            for row in connection.execute('SELECT i.source_id,COUNT(*) AS amount FROM item_read_states r '
                    'JOIN items i ON i.id=r.item_id WHERE r.is_read=1 GROUP BY i.source_id'):
                metrics[row['source_id']]['human_read'] = row['amount']
        examined, observed, fit, developed, developed_observed = defaultdict(set), defaultdict(set), defaultdict(set), defaultdict(set), defaultdict(set)
        changed_examined, changed_developed = defaultdict(set), defaultdict(set)
        if 'workflow_tasks' in tables:
            # Bir haberin tekrar işi, başka sorusu veya eski revizyonu paydayı şişirmez.
            for row in connection.execute('''SELECT w.item_id,i.source_id,w.kind,w.result_json,w.updated_at,
                    w.decision,w.decision_at FROM workflow_tasks w JOIN items i ON i.id=w.item_id
                    WHERE w.source_revision=i.revision AND w.status='ready' AND w.result_json IS NOT NULL
                    ORDER BY w.updated_at,w.id'''):
                identifier = row['source_id']
                try:
                    result = json.loads(row['result_json'])
                except (ValueError, TypeError):
                    continue
                if not isinstance(result, dict):
                    continue
                examined[identifier].add(row['item_id'])
                if row['updated_at'] >= metrics[identifier]['policy_since']:
                    observed[identifier].add(row['item_id'])
                if row['updated_at'] > metrics[identifier]['post_change_since']:
                    changed_examined[identifier].add(row['item_id'])
                if row['kind'] in {'interest', 'screen'}:
                    fit[identifier].discard(row['item_id'])
                    if isinstance(result, dict) and result.get('fit') == 'high':
                        fit[identifier].add(row['item_id'])
            # Daha yeni haber revizyonu, geçmişte kullanıcının geliştirmeye aldığı
            # katkıyı yok etmez. İnceleme paydası ise yalnız güncel revizyon içindir.
            for row in connection.execute("SELECT w.item_id,i.source_id,w.decision_at FROM workflow_tasks w "
                    "JOIN items i ON i.id=w.item_id WHERE w.decision='develop'"):
                identifier = row['source_id']
                developed[identifier].add(row['item_id'])
                if (row['decision_at'] or '') >= metrics[identifier]['policy_since']:
                    developed_observed[identifier].add(row['item_id'])
                if (row['decision_at'] or '') > metrics[identifier]['post_change_since']:
                    changed_developed[identifier].add(row['item_id'])
        if 'ai_results' in tables:
            for row in connection.execute('SELECT a.item_id,i.source_id,a.generated_at FROM ai_results a '
                    'JOIN items i ON i.id=a.item_id WHERE a.source_revision=i.revision AND a.status=\'hazir\' '
                    'AND a.result_json IS NOT NULL'):
                examined[row['source_id']].add(row['item_id'])
                if (row['generated_at'] or '') >= metrics[row['source_id']]['policy_since']:
                    observed[row['source_id']].add(row['item_id'])
                if (row['generated_at'] or '') > metrics[row['source_id']]['post_change_since']:
                    changed_examined[row['source_id']].add(row['item_id'])
        for identifier, values in metrics.items():
            values.update(examined=len(examined[identifier]), observed_examined=len(observed[identifier]),
                          developed=len(developed[identifier]), observed_developed=len(developed_observed[identifier]),
                          model_high_fit=len(fit[identifier]), post_change_examined=len(changed_examined[identifier]),
                          post_change_developed=len(changed_developed[identifier]))
        for row in connection.execute('''SELECT source_id,COUNT(*) AS collection_runs,
                SUM(CASE WHEN status IN ('error','partial') THEN 1 ELSE 0 END) AS errors,
                SUM(returned) AS returned,SUM(added) AS added,SUM(changed) AS changed,
                SUM(duplicates) AS duplicates,SUM(duration_seconds) AS duration_seconds
                FROM source_collection_runs GROUP BY source_id'''):
            metrics[row['source_id']].update({key: row[key] for key in row.keys() if key != 'source_id'})
        return metrics

    def _oneri(self, source, state, metrics, enabled, preference):
        def keep(reason):
            return {'action': 'keep', 'reason': reason, 'eligible': False}
        if source.get('kind') == 'discovery' or source['id'] in TEMEL_KAYNAKLAR:
            return keep('Temel veya kullanıcı tarafından korunan kaynak; otomatik seyrekleştirilmez.')
        if not source.get('enabled', True):
            return keep('Kaynak kullanıcı tercihiyle kapalı; otomatik açılmaz.')
        if not enabled:
            return keep('Otomatik kaynak sıklığı kapalı; ölçüm devam ediyor.')
        now = self.clock()
        hold = tarih(state.get('hold_until'))
        if hold and now < hold:
            return keep('Geri alınan kararın tekrar uygulanmaması için 30 günlük bekleme var.')
        previous = tarih(state.get('last_policy_at'))
        if previous and now - previous < timedelta(days=7):
            return keep('Kaynak sıklığı en fazla yedi günde bir değişir.')
        positive = metrics['observed_human_positive']
        negative = metrics['observed_human_negative']
        enough_negative = (metrics['observation_days'] >= 30 and metrics['observed_examined'] >= 20
                           and positive + negative >= 5 and negative >= 3
                           and positive / max(1, positive + negative) <= .1 and not metrics['observed_developed'])
        if state['lifecycle'] == 'trial':
            trial_days = (now - tarih(state['trial_started_at'] or state['observed_since'])).days
            if trial_days >= 14 and metrics['observed_examined'] >= 5 and (positive >= 2 or metrics['observed_developed'] >= 1):
                return {'action': 'promote', 'reason': 'En az 14 günlük denemede beş inceleme ve açık yarar kanıtı oluştu.',
                        'eligible': True}
        if state['protected']:
            return keep('Kullanıcı tarafından korunan kaynak; otomatik seyrekleştirilmez.')
        if preference.get('interval_hours') is not None:
            return keep('Kontrol aralığını kullanıcı belirledi; otomatik değiştirilmez.')
        if state['lifecycle'] == 'slow':
            recent_positive = metrics['post_change_human_positive']
            recent_negative = metrics['post_change_human_negative']
            strong_positive = (recent_positive >= 3 and recent_positive / max(1, recent_positive + recent_negative) >= .75)
            developed_again = metrics['post_change_developed'] >= 1 and recent_negative <= recent_positive
            if metrics['post_change_examined'] >= 5 and (strong_positive or developed_again):
                return {'action': 'promote', 'reason': 'Son seyrekleştirmeden sonraki en az beş incelemede güçlü yeni insan yarar kanıtı oluştu; yapılandırılmış sıklığa dönülür.',
                        'eligible': True, 'interval_hours': source.get('configured_interval_hours', source['interval_hours'])}
        if state['lifecycle'] == 'trial':
            if not enough_negative:
                return keep('Deneme 30 günü geçti; yarar veya olumsuzluk kanıtı yetersiz. Kaynak kararı bekleniyor; kapatılan deneme yeni aday için yer açar.'
                            if trial_days >= 30 else 'Yeni kaynak denemede; en az 14 gün, beş inceleme ve açık yarar kanıtı bekleniyor.')
        if metrics['observation_days'] < 30 or metrics['observed_examined'] < 20 or positive + negative < 5:
            return keep('Karar için en az 30 gün, 20 inceleme ve beş açık kullanıcı tepkisi gerekiyor; az yayın korunuyor.')
        if metrics['observed_developed'] or positive / max(1, positive + negative) > .1 or negative < 3:
            return keep('Açık yarar kanıtı var veya olumsuz kanıt yetersiz; mevcut sıklık korunuyor.')
        current = source['interval_hours']
        if current < 12:
            return keep('24 saat alt sınırı tek adımda iki kat değişim sınırını aşar; kullanıcı değerlendirmesi bekleniyor.')
        if current >= 168:
            return keep('Otomatik seyrekleştirme üst sınırına ulaşıldı; daha uzun kullanıcı aralığı korunur.')
        target = min(168, max(24, current * 2))
        return {'action': 'slow', 'reason': 'En az 30 gün ve 20 incelemede açık kullanıcı tepkilerinin en az %90’ı olumsuz; silmeden aralık uzatılır.',
                'eligible': True, 'interval_hours': target}

    def _donem(self, connection, start, end, measured_since):
        result = {'period_start': start.isoformat(), 'period_end': end.isoformat(), 'measured_since': measured_since,
                  'collection_runs': 0, 'errors': 0, 'added': None, 'changed': None, 'duplicates': None,
                  'model_calls': 0, 'reads': 0, 'human_positive': 0, 'human_negative': 0, 'developed': 0,
                  'changes': 0, 'cost_amount': None, 'tokens': None,
                  'label': 'Dönem içindeki olaylar; insan tepkileri haber başına son açık seçimdir. Toplama geçmişi ölçüm başlangıcından itibaren.'}
        bounds = (start.isoformat(), end.isoformat())
        row = connection.execute('''SELECT COUNT(*) AS collection_runs,SUM(CASE WHEN status IN ('error','partial') THEN 1 ELSE 0 END) AS errors,
            SUM(added) AS added,SUM(changed) AS changed,SUM(duplicates) AS duplicates FROM source_collection_runs
            WHERE started_at>=? AND started_at<?''', bounds).fetchone()
        result.update(dict(row))
        result['errors'] = result['errors'] or 0
        tables = _tablolar(connection)
        for table, field in (('ai_attempts', 'model_calls'), ('article_read_attempts', 'reads')):
            if table in tables:
                result[field] = connection.execute('SELECT COUNT(*) FROM ' + table + ' WHERE started_at>=? AND started_at<?', bounds).fetchone()[0]
        for row in connection.execute('''SELECT value,COUNT(*) AS amount FROM feedback_events f
                WHERE actor='yerel_kullanici' AND created_at>=? AND created_at<? AND id=(
                SELECT MAX(l.id) FROM feedback_events l WHERE l.item_id=f.item_id AND l.actor='yerel_kullanici'
                AND l.created_at>=? AND l.created_at<?) GROUP BY value''', bounds + bounds):
            field = {'yararli': 'human_positive', 'gereksiz': 'human_negative'}.get(row['value'])
            if field:
                result[field] = row['amount']
        if 'workflow_tasks' in tables:
            result['developed'] = connection.execute("SELECT COUNT(DISTINCT item_id) FROM workflow_tasks WHERE decision='develop' AND decision_at>=? AND decision_at<?", bounds).fetchone()[0]
        result['changes'] = connection.execute('SELECT COUNT(*) FROM source_learning_changes WHERE created_at>=? AND created_at<?', bounds).fetchone()[0]
        return result

    def _gorunum(self, connection):
        settings = dict(connection.execute('SELECT * FROM source_learning_settings WHERE id=1').fetchone())
        settings['enabled'] = bool(settings['enabled'])
        sources = self.depo.kaynaklar(connection)
        states = {row['source_id']: dict(row) for row in connection.execute('SELECT * FROM source_learning_state')}
        # Yeni elle eklenen kaynak ilk günlük ölçümde kalıcı duruma alınır; görünüş yazmaz.
        for source in sources:
            states.setdefault(source['id'], {'source_id': source['id'], 'observed_since': self.clock().isoformat(),
                'protected': source['id'] in TEMEL_KAYNAKLAR or source.get('kind') == 'discovery',
                'version': 0, 'lifecycle': 'regular', 'trial_started_at': None})
        preferences = {row['source_id']: dict(row) for row in connection.execute('SELECT * FROM source_preferences')}
        metrics = self._olcumler(connection, sources, states)
        rows = []
        for source in sources:
            state = states[source['id']]
            rows.append({'source_id': source['id'], 'name': source['name'], 'topic': source['topic'],
                         'protected': bool(state['protected']), 'version': state['version'],
                         'lifecycle': state['lifecycle'], 'enabled': source.get('enabled', True),
                         'trial_review_due': bool(state['lifecycle'] == 'trial' and
                             (self.clock() - tarih(state['trial_started_at'] or state['observed_since'])).days >= 30),
                         'effective_interval_hours': source['interval_hours'],
                         'configured_interval_hours': source.get('configured_interval_hours', source['interval_hours']),
                         'metrics': metrics[source['id']], 'evidence_label': KANIT_ACIKLAMASI,
                         'recommendation': self._oneri(source, state, metrics[source['id']], settings['enabled'], preferences.get(source['id'], {}))})
        changes = [dict(row) for row in connection.execute('SELECT * FROM source_learning_changes ORDER BY created_at DESC,id DESC LIMIT 100')]
        for change in changes:
            change['reverted'] = bool(change['reverted'])
            change['can_revert'] = not change['reverted'] and states[change['source_id']]['version'] == change['state_version']
        now = self.clock()
        local_day = now.astimezone(timezone(timedelta(hours=3))).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        summary = {key: sum(row['metrics'][key] for row in rows) for key in
                   ('examined', 'human_positive', 'human_negative', 'developed', 'model_calls', 'reads', 'collection_runs', 'errors')}
        summary.update(sources=len(rows), trial=sum(row['lifecycle'] == 'trial' for row in rows),
                       trial_waiting_review=sum(row['trial_review_due'] for row in rows),
                       protected=sum(row['protected'] for row in rows), slow=sum(row['lifecycle'] == 'slow' for row in rows),
                       measured_since=settings['started_at'], evidence_label=KANIT_ACIKLAMASI,
                       total_items=connection.execute('SELECT COUNT(*) FROM items').fetchone()[0],
                       cost_amount=None, tokens=None)
        return {'settings': settings, 'summary': summary, 'sources': rows, 'changes': changes,
                'daily': self._donem(connection, local_day, now + timedelta(microseconds=1), settings['started_at']),
                'weekly': self._donem(connection, now - timedelta(days=7), now + timedelta(microseconds=1), settings['started_at'])}

    def gorunum(self):
        with self.depo.baglanti() as connection:
            connection.execute('BEGIN')
            return self._gorunum(connection)

    def ayarlari_kaydet(self, enabled, expected_version):
        _surum(expected_version)
        if type(enabled) is not bool:
            raise ValueError('Otomatik kaynak sıklığı doğru/yanlış olmalı.')
        with self.depo.baglanti(write=True) as connection:
            current = connection.execute('SELECT * FROM source_learning_settings WHERE id=1').fetchone()
            if current['version'] != expected_version:
                raise KaynakOgrenmeCatismasi('Kaynak öğrenme ayarı değişti; güncel görünümü açın.')
            if bool(current['enabled']) != enabled:
                connection.execute('UPDATE source_learning_settings SET enabled=?,version=version+1 WHERE id=1', (int(enabled),))
                self.depo.olay_yaz(connection, 'kaynak_ogrenme_ayari', '1', {'enabled': enabled, 'version': expected_version + 1})
            return self._gorunum(connection)

    def koru(self, source_id, protected, expected_version):
        _surum(expected_version)
        if type(protected) is not bool:
            raise ValueError('Kaynak koruması doğru/yanlış olmalı.')
        with self.depo.baglanti(write=True) as connection:
            self._kaynaklari_hazirla(connection)
            row = connection.execute('SELECT * FROM source_learning_state WHERE source_id=?', (source_id,)).fetchone()
            if row is None:
                raise KeyError(source_id)
            if row['version'] != expected_version:
                raise KaynakOgrenmeCatismasi('Kaynak kararı değişti; güncel görünümü açın.')
            if bool(row['protected']) != protected:
                connection.execute('UPDATE source_learning_state SET protected=?,version=version+1 WHERE source_id=?',
                                   (int(protected), source_id))
                self.depo.olay_yaz(connection, 'kaynak_korumasi', source_id, {'protected': protected, 'version': expected_version + 1})
            return next(source for source in self._gorunum(connection)['sources'] if source['source_id'] == source_id)

    def kaynak_denemeye_al(self, source_id, provenance=None):
        """Yeni bulunan kaynağı süreli ve kanıt bekleyen deneme sınıfına alır; yeniden çağrı başlangıcı sıfırlamaz."""
        if provenance is not None and not isinstance(provenance, dict):
            raise ValueError('Kaynak keşif kaydı nesne olmalı.')
        encoded = json.dumps(provenance or {}, ensure_ascii=False)
        if len(encoded) > 4000:
            raise ValueError('Kaynak keşif kaydı çok uzun.')
        with self.depo.baglanti(write=True) as connection:
            self._kaynaklari_hazirla(connection)
            row = connection.execute('SELECT * FROM source_learning_state WHERE source_id=?', (source_id,)).fetchone()
            if row is None:
                raise KeyError(source_id)
            if row['trial_started_at'] is None and not row['protected']:
                connection.execute("UPDATE source_learning_state SET lifecycle='trial',trial_started_at=?,provenance_json=?,version=version+1 WHERE source_id=?",
                                   (self.clock().isoformat(), encoded, source_id))
                self.depo.olay_yaz(connection, 'kaynak_deneme_basladi', source_id, {'version': row['version'] + 1})
            return next(source for source in self._gorunum(connection)['sources'] if source['source_id'] == source_id)

    def geri_al(self, change_id, expected_version):
        _surum(expected_version)
        with self.depo.baglanti(write=True) as connection:
            change = connection.execute('SELECT * FROM source_learning_changes WHERE id=?', (change_id,)).fetchone()
            if change is None:
                raise KeyError(change_id)
            state = connection.execute('SELECT * FROM source_learning_state WHERE source_id=?', (change['source_id'],)).fetchone()
            if change['version'] != expected_version or change['reverted'] or state['version'] != change['state_version']:
                raise KaynakOgrenmeCatismasi('Bu karar değişti veya ardından başka bir karar verildi; eski durum yeni tercihi ezemez.')
            connection.execute('UPDATE source_learning_state SET interval_hours=?,base_interval_hours=?,lifecycle=?,hold_until=?,version=version+1 WHERE source_id=?',
                               (change['before_overlay'], change['before_base'], change['before_lifecycle'],
                                (self.clock() + timedelta(days=30)).isoformat(), change['source_id']))
            connection.execute('UPDATE source_learning_changes SET reverted=1,reverted_at=?,version=version+1 WHERE id=?',
                               (self.clock().isoformat(), change_id))
            self._vadeyi_yenile(connection, change['source_id'])
            self.depo.olay_yaz(connection, 'kaynak_karari_geri_alindi', change['source_id'], {'change_id': change_id})
            return self._gorunum(connection)

    def _vadeyi_yenile(self, connection, source_id):
        source = next(source for source in self.depo.kaynaklar(connection) if source['id'] == source_id)
        if not source.get('retry_pending') and (checked := tarih(source.get('last_checked_at'))):
            backoff = min(168, 2 ** min(int(source.get('failures', 0)), 7)) if source.get('last_error') else 0
            connection.execute('UPDATE sources SET next_due_at=? WHERE id=?',
                               ((checked + timedelta(hours=max(source['interval_hours'], backoff))).isoformat(), source_id))

    def degerlendir(self):
        """Günlük görünüşü atomik saklar; yeterli kanıtı olan kaynakları haftada en çok bir kez değiştirir."""
        with self.depo.baglanti(write=True) as connection:
            self._kaynaklari_hazirla(connection)
            view = self._gorunum(connection)
            now = self.clock()
            previous = tarih(view['settings'].get('last_evaluated_at'))
            if previous is None or now - previous >= timedelta(days=7):
                for source in view['sources']:
                    recommendation = source['recommendation']
                    if not recommendation['eligible']:
                        continue
                    state = connection.execute('SELECT * FROM source_learning_state WHERE source_id=?', (source['source_id'],)).fetchone()
                    target = recommendation.get('interval_hours', source['effective_interval_hours'])
                    lifecycle = 'regular' if recommendation['action'] == 'promote' else 'slow'
                    interval = None if lifecycle == 'regular' else target
                    connection.execute('UPDATE source_learning_state SET interval_hours=?,base_interval_hours=?,lifecycle=?,last_policy_at=?,version=version+1 WHERE source_id=?',
                                       (interval, source['configured_interval_hours'], lifecycle, now.isoformat(), source['source_id']))
                    connection.execute('INSERT INTO source_learning_changes(id,source_id,before_interval,after_interval,before_lifecycle,after_lifecycle,before_overlay,before_base,reason,created_at,state_version) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                                       (uuid.uuid4().hex, source['source_id'], source['effective_interval_hours'], target,
                                        state['lifecycle'], lifecycle, state['interval_hours'], state['base_interval_hours'],
                                        recommendation['reason'], now.isoformat(), state['version'] + 1))
                    if lifecycle == 'regular':
                        self._vadeyi_yenile(connection, source['source_id'])
                connection.execute('UPDATE source_learning_settings SET last_evaluated_at=? WHERE id=1', (now.isoformat(),))
            view = self._gorunum(connection)
            day = now.astimezone(timezone(timedelta(hours=3))).date().isoformat()
            connection.execute('INSERT INTO source_learning_snapshots VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET created_at=excluded.created_at,report_json=excluded.report_json',
                               (day, now.isoformat(), json.dumps(view, ensure_ascii=False)))
            return view
