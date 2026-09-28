"""CLI для генерации и проверки ID без веб-интерфейса.

Перед первым использованием создайте справочники кодов (JSON-конфиги):
    python cli.py codes-init            # создаст config/producers.json и companies.json
После этого впишите свои названия и коды, например:
    "Ромашка-Завод": "RZ"  (в producers.json),  "Вектор-Телеком": "VT" (в companies.json)

Примеры:
    python cli.py generate --producer "Ромашка-Завод" \
        --location "Москва" --company "Вектор-Телеком" --serial SN-00123 \
        --port TN_A --site 6
    python cli.py verify RZ4KF-VT-A2238HYEQ
    (дата не вводится — фиксируется автоматически при генерации и
     восстанавливается из внутренней базы при проверке)
"""

from __future__ import annotations

import argparse
import sys

from app import db
from app.codes import ensure_configs
from app.idgen import extract_parts, make_id, verify_checksum


def cmd_codes_init(args):
    files = ensure_configs(force=args.force)
    if args.force:
        print("Справочники перезаписаны шаблонами:")
    else:
        print("Справочники созданы (существующие не тронуты):")
    for name, path in files.items():
        print(f"  {name}: {path}")
    print("Заполните их своими названиями и кодами (2 символа из алфавита base31).")
    return 0


def cmd_generate(args):
    db.init_db()
    res = db.register(args.producer, args.location, args.company,
                      args.serial, args.port, args.site)
    rec = res["record"]
    print(f"ID:            {rec['id']}")
    print(f"Короткий код:  {make_id(rec['producer'], rec['location'], rec['company'], rec['serial'], rec['port'], rec['site'], dt=rec['date'])['short']}")
    print(f"Дата (авто):   {rec['date']}")
    print(f"Каноническая:  {rec['canonical']}")
    print(f"Статус:        {'создан новый' if res['created'] else 'уже существовал в базе'}")


def cmd_preview(args):
    rec = make_id(args.producer, args.location, args.company,
                  args.serial, args.port, args.site)
    print(f"ID:       {rec['id']}")
    print(f"Короткий: {rec['short']}")
    print(f"Дата (авто): {rec['fields']['date']}")


def cmd_verify(args):
    ok = verify_checksum(args.id)
    print(f"Контрольный код: {'✔ верен' if ok else '✘ не верен'}")
    if ok:
        print(f"Разбор:          {extract_parts(args.id)}")
        record = db.lookup(args.id)
        if record:
            print("В базе:          ✔ найдена запись")
            for k in ("producer", "date", "location", "company", "serial",
                      "port", "site", "created_at"):
                if k not in record:
                    continue
                print(f"  {k:14} {record[k]}")
        else:
            print("В базе:          ✘ запись отсутствует")
    return 0 if ok else 1


def build_parser():
    p = argparse.ArgumentParser(description="Генератор уникальных ID")
    sub = p.add_subparsers(dest="cmd", required=True)

    ci = sub.add_parser("codes-init",
                        help="Создать JSON-справочники config/producers.json и companies.json")
    ci.add_argument("--force", action="store_true", help="Перезаписать существующие шаблонами")
    ci.set_defaults(func=cmd_codes_init)

    def add_gen(sp):
        sp.add_argument("--producer", required=True, help="Производитель")
        sp.add_argument("--location", required=True, help="Место положения")
        sp.add_argument("--company", required=True, help="Компания")
        sp.add_argument("--serial", required=True, help="Серийный номер")
        sp.add_argument("--port", required=True, help="Порт: TN_A / TN_B / TN_C")
        sp.add_argument("--site", required=True, help="Номер площадки (целое число)")

    g = sub.add_parser("generate", help="Сгенерировать ID и сохранить в базу (дата — автоматически)")
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
