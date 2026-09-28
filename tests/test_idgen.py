import pytest

from app.idgen import (
    ALPHABET, TOTAL_LEN, extract_parts, make_id, normalize_id, verify_checksum,
)

BASE = dict(producer="Роботех", dt="15.03.2026", location="Москва",
            company="Технопарк", serial="SN-00123")


def test_format():
    r = make_id(**BASE)
    compact = normalize_id(r["id"])
    assert len(compact) == TOTAL_LEN
    assert all(ch in ALPHABET for ch in compact)
    assert r["id"].count("-") == 3


def test_deterministic_and_normalized():
    a = make_id(**BASE)["id"]
    b = make_id(producer="  роботех ", dt="2026-03-15", location="москва",
                company="ТЕХНОПАРК", serial=" sn-00123 ")["id"]
    assert a == b


@pytest.mark.parametrize("field,value", [
    ("serial", "SN-00124"), ("producer", "Роботех-2"), ("location", "Казань"),
    ("company", "Иная"), ("dt", "16.03.2026"),
])
def test_any_field_change_changes_id(field, value):
    base = make_id(**BASE)["compact"]
    other = make_id(**{**BASE, field: value})["compact"]
    assert base != other


def test_checksum_valid_and_detects_tampering():
    r = make_id(**BASE)["id"]
    assert verify_checksum(r)
    tampered = r[:-1] + ("A" if r[-1] != "A" else "B")
    assert not verify_checksum(tampered)


def test_secret_changes_checksum():
    public = make_id(**BASE)["id"]
    secret = make_id(**BASE, secret="topsecret")["id"]
    assert public[:12] == secret[:12]          # тело одинаковое
    assert public[12:] != secret[12:]          # контрольный код разный
    assert verify_checksum(secret, secret="topsecret")
    assert not verify_checksum(secret)         # без секрета не проходит


def test_date_formats():
    assert make_id(**{**BASE, "dt": "2026-03-15"})["id"] == \
           make_id(**{**BASE, "dt": "15.03.2026"})["id"] == \
           make_id(**{**BASE, "dt": "15-3-2026"})["id"]


@pytest.mark.parametrize("bad", ["32.13.2026", "не дата", "2026-02-30"])
def test_invalid_date(bad):
    with pytest.raises(ValueError):
        make_id(**{**BASE, "dt": bad})


@pytest.mark.parametrize("empty", ["producer", "location", "company", "serial"])
def test_required_fields(empty):
    with pytest.raises(ValueError):
        make_id(**{**BASE, empty: "   "})


def test_extract_parts_roundtrip():
    r = make_id(**BASE)
    parts = extract_parts(r["id"])
    assert parts["checksum"] == r["compact"][12:]
    assert r["compact"].startswith(parts["producer_prefix"])


def test_uniqueness_bulk():
    ids = {make_id(producer=f"P{i}", dt="2026-01-01", location="L",
                   company="C", serial=f"S{i}")["compact"] for i in range(3000)}
    assert len(ids) == 3000
