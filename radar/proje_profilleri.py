"""Haber uygunluğu için sır içermeyen proje hedeflerini ve uygulama sınırlarını tanımlar."""

import hashlib
import json

from radar.konular import KONULAR, konu

def profil(topic):
    definition = konu(topic if topic in KONULAR else "ai")
    return {"project_name": definition["label_tr"], **definition["profile"]}

def profil_izi(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

def profiller():
    return [{'topic': topic, 'label': konu(topic)['label_tr'], 'label_en': konu(topic)['label_en'], **profil(topic)}
            for topic in KONULAR]


def on_arastirma_profili(project):
    """NVIDIA'ya yalnız genel ilgi özetini verir; donanım ve geliştirme ayrıntılarını taşımaz."""
    name = project.get('project_name', '')
    for topic in KONULAR:
        known = profil(topic)
        if name == known['project_name']:
            return {'project_name': name, 'summary': known['summary']}
    return {'project_name': 'Genel ilgi alanları',
            'summary': 'Yazılım, yapay zekâ, güvenlik, veri, otomasyon, robotik, tarım ve medya alanlarındaki yararlı gelişmeleri takip ediyoruz.'}
