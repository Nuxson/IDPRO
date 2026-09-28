import pytest

from app.idgen import (
    ALPHABET, BODY_LEN, PREFIX_LEN, SITE_MAX, TOTAL_LEN, decode_site,
    extract_parts, make_id, normalize_id, site_code, suggest_code,
    suggest_port, verify_checksum,
)
from app.codes import CodeRegistry, CodesError, load_codes, save_codes
from app.idgen import site_excluded as _site_excluded_fn

idgen_SITE_EXCLUDED_LIST = _site_excluded_fn()

BASE = dict(producer="Ромашка", location="Москва",
            company="Вектор", serial="SN-00123", port="TN_A", site="5")


@pytest.fixture(autouse=True)
def registries(tmp_path, monkeypatch):
    """Справочники-конфиги в tmp: никаких реальных брендов — только условные имена."""
    p = tmp_path / "producers.json"
    c = tmp_path / "companies.json"
    pr = tmp_path / "ports.json"
    save_codes(p, {"Ромашка": "RK", "Вектор": "VT", "Пример-Производитель": "PP",
                   "P1": "PA", "P2": "PB", "P3": "PC", "P4": "PD", "P5": "PE",
                   "P6": "PF", "P7": "PG", "P8": "PH", "P9": "PJ", "P10": "PM",
                   "P11": "PN", "P12": "NP", "P13": "NQ", "P14": "NR", "P15": "NS",
                   "P16": "NT", "P17": "NV", "P18": "NW", "P19": "NX", "P20": "NZ",
                   "P21": "RZ", "P22": "SA"})
    save_codes(c, {"Вектор": "VK", "Иная": "NA", "Пример-Компания": "PK"})
    save_codes(pr, {"TN_A": "A", "TN_B": "B", "TN_C": "C", "PORT_D": "D"})
    reg = CodeRegistry(p, c, pr)
    from app import idgen
    monkeypatch.setattr(idgen, "default_registry", reg)
    return reg


def test_format():
    r = make_id(**BASE, dt="15.03.2026")
    compact = normalize_id(r["id"])
    assert len(compact) == TOTAL_LEN == 19
    assert all("2" <= ch <= "9" or "A" <= ch <= "Z" for ch in compact)  # без 0/O/1/I/L
    assert r["id"] == "-".join([compact[0:4], compact[4:8], compact[8:12],
                    compact[12:16], compact[16:19]])   # блоки 4-4-4-4-3


def test_date_is_automatic():
    """Дата не вводится: без dt подставляется сегодня, формат валиден."""
    from datetime import date
    r = make_id(**BASE)
    assert r["fields"]["date"] == date.today().isoformat()
    assert verify_checksum(r["id"])


def test_deterministic_and_normalized():
    a = make_id(**BASE, dt="15.03.2026")["id"]
    b = make_id(producer="  ромашка ", dt="2026-03-15", location="москва",
                company="ВЕКТОР", serial=" sn-00123 ", port="tn_a", site="5")["id"]
    assert a == b


@pytest.mark.parametrize("field,value", [
    ("serial", "SN-00124"), ("producer", "Вектор"), ("location", "Казань"),
    ("company", "Иная"), ("dt", "16.03.2026"), ("port", "TN_B"), ("site", "8"),
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
    for s in ("0", "1", "2", "99", "100", "4242", "99999", str(SITE_MAX)):
        r = make_id(**{**BASE, "site": s}, dt="15.03.2026")
        assert verify_checksum(r["id"]), s
        parts = extract_parts(r["id"])
        assert parts["site_number"] == int(s), s       # точное восстановление из кода
    assert site_code("6") == "2228"
    assert site_code("42") == "223A"
    assert decode_site(site_code("99999")) == 99999
    assert SITE_MAX > 99999 and SITE_MAX < 34 ** 4
    assert decode_site(site_code(str(SITE_MAX))) == SITE_MAX


@pytest.mark.parametrize("bad", ["abc", "-5", "1.5", str(SITE_MAX + 1), "999999999999"] +
                         [str(v) for v in sorted(idgen_SITE_EXCLUDED_LIST)[:20]])   # резерв кода даты (выборка)
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
    # уникальность по серийному номеру при одинаковых прочих полях
    ids = {make_id(**{**BASE, "serial": f"S{i}"}, dt="15.03.2026")["compact"]
           for i in range(3000)}
    assert len(ids) == 3000


def test_suggest_code_avoids_ambiguous_letters():
    # названия на L/C/P не должны порождать коды с неоднозначными буквами
    for name in ("L", "Lada", "C", "Cars", "Irina", "Omsk", "Lucky-Cats"):
        code = suggest_code(name)
        assert len(code) == 2 and all(ch in ALPHABET for ch in code), (name, code)


def test_suggest_port_single_letter():
    code = suggest_port("PORT_X")
    assert len(code) == 1 and code in ALPHABET
    assert code not in "ABC"          # не конфликтует со стандартными TN_A/B/C
    assert suggest_port("PORT_X") == suggest_port("port-x")   # детерминированно


def test_ports_registry(tmp_path):
    from app import idgen
    pp = tmp_path / "ports.json"
    save_codes(pp, {"TN_A": "A", "TN_X": "X"})
    reg = CodeRegistry(tmp_path / "nope.json", tmp_path / "nope2.json", pp)
    assert reg.port_code("TN_X") == "X"
    with pytest.raises(CodesError):
        reg.port_code("TN_ZZZ")
    # генерация через пользовательский порт из справочника
    monkey_reg = reg  # producer/company справочники для make_id — текущие (fixture)
    r = make_id(**{**BASE, "port": "TN_X"}, dt="15.03.2026",
                registry_obj=CodeRegistry(idgen.default_registry.producers_path,
                                          idgen.default_registry.companies_path, pp))
    assert extract_parts(r["id"])["port"] == "X"


def test_load_codes_reports_all_problems(tmp_path):
    p = tmp_path / "bad.json"
    save_codes(p, {"OK": "KK", "Bad1": "LQ", "Bad2": "0O"})
    with pytest.raises(CodesError) as e:
        load_codes(p)
    msg = str(e.value)
    assert "'LQ'" in msg and "'0O'" in msg   # список всех проблемных записей
