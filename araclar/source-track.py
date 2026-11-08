"""Explicit local K0 catalog inspection/check; never runs a model or publishes content."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radar.kaynak_izleme import SourceTracker, TrackingSource


def unique_fields(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError("Tekrarlanan yapılandırma alanı.")
        result[key] = value
    return result


def load_config(path):
    with Path(path).open('rb') as stream:
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise ValueError("Kaynak kataloğu sınırı aşıyor.")
    value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_fields)
    if (not isinstance(value,dict) or set(value) != {'schema','daily_limit','sources'}
            or value['schema'] != 'devtrellis-source-tracking-v2' or not isinstance(value['sources'],list)):
        raise ValueError("K0 kaynak kataloğu geçersiz.")
    return [TrackingSource(**item) for item in value['sources']],value['daily_limit']


class Parser(argparse.ArgumentParser):
    def error(self,message):
        self.exit(2,'K0 seçenekleri geçersiz. Kullanım için --help çalıştırın.\n')


def main():
    parser=Parser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--source',required=True)
    parser.add_argument('--check',action='store_true',help='Explicitly allow the selected public source check.')
    parser.add_argument('--max-age',type=int,default=3600)
    args=parser.parse_args()
    sources,limit=load_config(args.config)
    tracker=SourceTracker(args.db,sources,daily_limit=limit)
    result=(tracker.check if args.check else tracker.read)(args.source,max_age_seconds=args.max_age)
    print(json.dumps(result,ensure_ascii=False,separators=(',',':')))
    if args.check and result.get('check_started') and not result.get('check_succeeded'):
        return 1
    if args.check and not result.get('check_started') and not result.get('fresh'):
        return 2  # Deferred by another check, budget or retry delay; not success.
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('K0 işlemi tamamlanmadı. Kamu kataloğunu ve ayrı yerel depo yolunu kontrol edin.',file=sys.stderr)
        raise SystemExit(1)
