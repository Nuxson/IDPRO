import pytest

from app.idgen import (
    ALPHABET, BODY_LEN, PREFIX_LEN, TOTAL_LEN, decode_site, extract_parts,
    make_id, normalize_id, site_code, suggest_code, verify_checksum,
)
from app.codes import CodeRegistry, CodesError, load_codes, save_codes

BASE = dict(producer="Ромашка", location="Москва",
            company="Вектор", serial="SN-00123", port="TN_A", site="6")


@pytest.fixture(autouse=True)
def registries(tmp_path, monkeypatch):
    """Справочники-конфиги в tmp: никаких реальных брендов — только условные имена."""
    p = tmp_path / "producers.json"
    c = tmp_path / "companies.json"
    save_codes(p, {"Ромашка": "RK", "Вектор": "VT", "Пример-Производитель": "PP",
                   "L": "LZ", "C": "CF", "P": "PA"})
    save_codes(c, {"Вектор": "VK", "Иная": "IN", "Пример-Компания": "PK",
                   "C": "CA", "L": "LK"})
    reg = CodeRegistry(p, c)
    from app import idgen
    monkeypatch.setattr(idgen, "default_registry", reg)
    return reg


def test_format():
    r = make_id(**BASE, dt="15.03.2026")
    compact = normalize_id(r["id"])
    assert len(compact) == TOTAL_LEN == 17
    assert all(ch in ALPHABET or ch in "ILOUY" for ch in compact)  # компактный алфавит допускает ILOUY в числовых сегментах
    assert r["id"] == "-".join(compact[i:i+4] for i in range(0, 17, 4))  # блоки по 4, последний — 3


def test_date_is_automatic():
    """Дата не вводится: без dt подставляется сегодня, формат валиден."""
    from datetime import date
    r = make_id(**BASE)
    assert r["fields"]["date"] == date.today().isoformat()
    assert verify_checksum(r["id"])


def test_deterministic_and_normalized():
    a = make_id(**BASE, dt="15.03.2026")["id"]
    b = make_id(producer="  роботех ", dt="2026-03-15", location="москва",
                company="ТЕХНОПАРК", serial=" sn-00123 ", port="tn_a", site="6")["id"]
    assert a == b


@pytest.mark.parametrize("field,value", [
    ("serial", "SN-00124"), ("producer", "Роботех-2"), ("location", "Казань"),
    ("company", "Иная"), ("dt", "16.03.2026"), ("port", "TN_B"), ("site", "7"),
])
def test_any_field_change_changes_id(field, value):
    base = make_id(**BASE, dt="15.03.2026")["compact"]
    other = make_id(**{**BASE, "dt": "15.03.2026", field: value})["compact"]
    assert base != other


def test_checksum_valid_and_detects_tampering():
    r = make_id(**BASE, dt="15.03.2026")["id"]
    assert verify_checksum(r)
    tampered = r[:-1] + ("A" if r[-1] != "A" else "B")
    assert not verify_checksum(tampered)


def test_secret_changes_checksum():
    public = make_id(**BASE, dt="15.03.2026")["id"]
    secret = make_id(**BASE, dt="15.03.2026", secret="topsecret")["id"]
    assert public[:BODY_LEN] == secret[:BODY_LEN]      # тело одинаковое
    assert public[BODY_LEN:] != secret[BODY_LEN:]      # контрольный код разный
    assert verify_checksum(secret, secret="topsecret")
    assert not verify_checksum(secret)                 # без секрета не проходит


def test_site_range_zero_to_max():
    for s in ("0", "1", "6", "99", "100", "4242", "99999"):
        r = make_id(**{**BASE, "site": s}, dt="15.03.2026")
        assert verify_checksum(r["id"]), s
        parts = extract_parts(r["id"])
        assert parts["site_number"] == int(s), s       # точное восстановление из кода
    assert site_code("6") == "2228"
    assert site_code("42") == "223A"
    assert decode_site(site_code("99999")) == 99999


@pytest.mark.parametrize("bad", ["abc", "-5", "1.5", "100000", "999999999999"])
def test_invalid_site(bad):
    with pytest.raises(ValueError):
        make_id(**{**BASE, "site": bad}, dt="15.03.2026")


def test_short_code_shows_plain_site():
    r = make_id(**{**BASE, "site": "42"}, dt="07.08.2026")
    assert r["short"].split("-")[-1] == "42"


@pytest.mark.parametrize("fmt", ["2026-03-15", "15.03.2026", "15-3-2026"])
def test_dt_service_param_formats(fmt):
    """Служебный dt (для тестов/БД) принимает разные форматы; пользователь его не вводит."""
    assert make_id(**BASE, dt="15.03.2026")["id"] == make_id(**BASE, dt=fmt)["id"]


@pytest.mark.parametrize("empty", ["producer", "location", "company", "serial", "port", "site"])
def test_required_fields(empty):
    with pytest.raises(ValueError):
        make_id(**{**BASE, empty: "   "}, dt="15.03.2026")


def test_extract_parts_roundtrip():
    r = make_id(**BASE, dt="15.03.2026")
    parts = extract_parts(r["id"])
    assert parts["checksum"] == r["compact"][BODY_LEN:]
    assert parts["hash"] == r["compact"][PREFIX_LEN:BODY_LEN]
    assert r["compact"].startswith(parts["producer_prefix"])
    assert parts["port_name"] == "TN_A"


def test_uniqueness_bulk():
    ids = {make_id(producer=f"P{i}", location="L", company="C",
                   serial=f"S{i}", port="TN_A", site=str(i % 1000))["compact"]
           for i in range(3000)}
    assert len(ids) == 3000
