"""CLI для генерации и проверки ID без веб-интерфейса.

Примеры:
    python cli.py generate --producer "Роботех" --date 15.03.2026 \
        --location "Москва" --company "Технопарк" --serial SN-00123
    python cli.py verify RQTE-MQ9Q-HSEW-FG
"""

from __future__ import annotations

import argparse
import sys

from app import db
from app.idgen import extract_parts, make_id, verify_checksum


def cmd_generate(args):
    db.init_db()
    res = db.register(args.producer, args.date, args.location, args.company, args.serial)
    rec = res["record"]
    print(f"ID:            {rec['id']}")
    print(f"Каноническая:  {rec['canonical']}")
    print(f"Статус:        {'создан новый' if res['created'] else 'уже существовал в базе'}")


def cmd_preview(args):
    rec = make_id(args.producer, args.date, args.location, args.company, args.serial)
    print(rec["id"])


def cmd_verify(args):
    ok = verify_checksum(args.id)
    print(f"Контрольный код: {'✔ верен' if ok else '✘ не верен'}")
    if ok:
        print(f"Разбор:          {extract_parts(args.id)}")
        record = db.lookup(args.id)
        if record:
            print("В базе:          ✔ найдена запись")
            for k in ("producer", "date", "location", "company", "serial", "created_at"):
                print(f"  {k:14} {record[k]}")
        else:
            print("В базе:          ✘ запись отсутствует")
    return 0 if ok else 1


def build_parser():
    p = argparse.ArgumentParser(description="Генератор уникальных ID")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_gen(sp):
        sp.add_argument("--producer", required=True, help="Производитель")
        sp.add_argument("--date", required=True, help="Дата (ДД.ММ.ГГГГ или ГГГГ-ММ-ДД)")
        sp.add_argument("--location", required=True, help="Место положения")
        sp.add_argument("--company", required=True, help="Компания")
        sp.add_argument("--serial", required=True, help="Серийный номер")

    g = sub.add_parser("generate", help="Сгенерировать ID и сохранить в базу")
    add_gen(g); g.set_defaults(func=cmd_generate)

    v = sub.add_parser("preview", help="Сгенерировать ID без сохранения")
    add_gen(v); v.set_defaults(func=cmd_preview)

    c = sub.add_parser("verify", help="Проверить ID (CRC + поиск в базе)")
    c.add_argument("id"); c.set_defaults(func=cmd_verify)
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    try:
        sys.exit(args.func(args) or 0)
    except ValueError as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(2)
