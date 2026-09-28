/**
 * idgen.js — JS-порт ядра генерации уникальных ID (работает в браузере и Node.js).
 *
 * Полностью совместим с Python-версией app/idgen.py: те же входные данные → тот же код.
 * Формат ID: XXXX-XXXX-XXXX-CCKK (14 символов base31, блоки по 4).
 *
 * Состав кода:
 *   [0:2]   префикс производителя (транслит)
 *   [2:4]   префикс компании
 *   [4:6]   префикс места положения
 *   [6:12]  хеш-часть SHA-256 от канонической строки всех полей (base31)
 *   [12:14] контрольный код HMAC-SHA256 от тела ID (аналог CRC у серийных номеров / IMEI)
 *
 * Использование (браузер):
 *   const res = await IdGen.makeId({ producer:'Роботех', date:'2026-09-28',
 *                                    location:'Москва', company:'Технопарк', serial:'SN-00123' });
 *   const ok  = await IdGen.verifyChecksum(res.id);
 */
(function (global, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();                       // CommonJS (Node)
  } else {
    global.IdGen = factory();                         // Браузер: window.IdGen
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // ---------- Константы (идентичны idgen.py) ----------
  const ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'; // без неоднозначных 0/O, 1/I/L
  const BASE = ALPHABET.length;                      // 31
  const SEP = '-';
  const BLOCK = 4;
  const CHECK_LEN = 2;
  const HASH_LEN = 6;
  const PREFIX_LEN = 2;
  const BODY_LEN = PREFIX_LEN * 3 + HASH_LEN;        // 12
  const TOTAL_LEN = BODY_LEN + CHECK_LEN;            // 14

  const TMAP = {
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'E',
    'Ж': 'Z', 'З': 'Z', 'И': 'K', 'Й': 'K', 'К': 'K', 'Л': 'Q', 'М': 'M',
    'Н': 'N', 'О': 'Q', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
    'Ф': 'F', 'Х': 'H', 'Ц': 'C', 'Ч': 'C', 'Ш': 'W', 'Щ': 'W', 'Ъ': 'Q',
    'Ы': 'Y', 'Ь': 'Q', 'Э': 'E', 'Ю': 'U', 'Я': 'U',
  };

  // ---------- Хеш-примитивы: WebCrypto (основной путь), чистый JS (фолбэк для Node) ----------
  async function sha256Bytes(bytes) {
    if (typeof crypto !== 'undefined' && crypto.subtle) {
      const buf = await crypto.subtle.digest('SHA-256', bytes);
      return new Uint8Array(buf);
    }
    return _sha256Pure(bytes);
  }

  // Чистый JS SHA-256 (используется только если недоступен WebCrypto)
  function _sha256Pure(msg) {
    const rotr = (x, n) => (x >>> n) | (x << (32 - n));
    const kk = [
      0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
      0xd807aa98, 0x12835b01, 0x243185be, 0x2341b538, 0x5948720c, 0x1fc649a3, 0x5d5ab729, 0x6bca5e90,
      0x748f82ee, 0x78de5eee, 0x84c87814, 0x9ccdb0a3, 0xa2bff8d1, 0xaebf8657, 0xc5ef0bfe, 0xd69be1c9,
      0xfee6b3df, 0x0f4d50e6, 0x10dbacd, 0x19a4c116, 0x1e376c69, 0x2748774d, 0x2885dec7, 0x391c0cb3,
      0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90bef3ba,
      0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ];
    let h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    const len = msg.length;
    const withPad = new Uint8Array((((len + 8) >> 6) + 1) * 64);
    withPad.set(msg); withPad[len] = 0x80;
    const bits = BigInt(len) * 8n;
    for (let i = 0; i < 8; i++) withPad[withPad.length - 1 - i] = Number((bits >> BigInt(8 * i)) & 0xffn);
    const w = new Uint32Array(64);
    for (let off = 0; off < withPad.length; off += 64) {
      for (let i = 0; i < 16; i++) {
        w[i] = (withPad[off + i * 4] << 24) | (withPad[off + i * 4 + 1] << 16) |
               (withPad[off + i * 4 + 2] << 8) | withPad[off + i * 4 + 3];
      }
      for (let i = 16; i < 64; i++) {
        const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      let [a, b, c, d, e, f, g, hh] = h;
      for (let i = 0; i < 64; i++) {
        const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const ch = (e & f) ^ (~e & g);
        const t1 = (hh + S1 + ch + kk[i] + w[i]) >>> 0;
        const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (S0 + maj) >>> 0;
        hh = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      h = h.map((x, i) => (x + [a, b, c, d, e, f, g, hh][i]) >>> 0);
    }
    const out = new Uint8Array(32);
    h.forEach((x, i) => {
      out[i * 4] = x >>> 24; out[i * 4 + 1] = (x >>> 16) & 255;
      out[i * 4 + 2] = (x >>> 8) & 255; out[i * 4 + 3] = x & 255;
    });
    return out;
  }

  // HMAC-SHA256 (для офлайн-проверки с секретом; при secret=null не вызывается)
  async function hmacSha256(keyStr, msgBytes) {
    const enc = new TextEncoder();
    if (typeof crypto !== 'undefined' && crypto.subtle) {
      const key = await crypto.subtle.importKey('raw', enc.encode(keyStr),
        { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
      return new Uint8Array(await crypto.subtle.sign('HMAC', key, msgBytes));
    }
    let key = enc.encode(keyStr);
    const block = 64;
    if (key.length > block) key = _sha256Pure(key);
    const k = new Uint8Array(block); k.set(key);
    const ipad = new Uint8Array(block), opad = new Uint8Array(block);
    for (let i = 0; i < block; i++) { ipad[i] = k[i] ^ 0x36; opad[i] = k[i] ^ 0x5c; }
    const inner = new Uint8Array(block + msgBytes.length);
    inner.set(ipad); inner.set(msgBytes, block);
    const innerHash = _sha256Pure(inner);
    const outer = new Uint8Array(block + 32);
    outer.set(opad); outer.set(innerHash, block);
    return _sha256Pure(outer);
  }

  // ---------- Утилиты нормализации (1-в-1 с Python) ----------
  const norm = (v) => String(v ?? '').trim().toUpperCase().split(/\s+/).filter(Boolean).join(' ');
  const translit = (s) => [...s].map(ch => TMAP[ch] ?? ch).join('');

  function prefix(text, n = PREFIX_LEN) {
    const cleaned = translit(norm(text)).replace(/[^A-Z0-9]/g, '');
    let p = [...cleaned.slice(0, n)].map(ch => ALPHABET.includes(ch) ? ch : 'Q').join('');
    while (p.length < n) p += 'Q';
    return p;
  }

  function normDate(value) {
    const v = norm(value).replace(/[/.]/g, '-');
    const m = /^(\d{1,4})-(\d{1,2})-(\d{1,4})$/.exec(v);
    if (!m) throw new Error(`Некорректный формат даты: ${JSON.stringify(value)} (ожидается ДД.ММ.ГГГГ или ГГГГ-ММ-ДД)`);
    let y, mo, d;
    if (m[1].length === 4) { y = +m[1]; mo = +m[2]; d = +m[3]; }
    else { d = +m[1]; mo = +m[2]; y = +m[3]; }
    const dt = new Date(Date.UTC(y, mo - 1, d));
    if (y < 1 || dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) {
      throw new Error(`Несуществующая дата: ${JSON.stringify(value)}`);
    }
    return `${String(y).padStart(4, '0')}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
  }

  // Представление числа в base31 фиксированной длины (BigInt — точность как у Python int)
  function toBase31(number, length) {
    let n = BigInt(number);
    const out = [];
    for (let i = 0; i < length; i++) {
      out.push(ALPHABET[Number(n % BigInt(BASE))]);
      n /= BigInt(BASE);
    }
    return out.reverse().join('');
  }

  const canonicalString = (producer, isoDate, location, company, serial) =>
    ['v1', producer, isoDate, location, company, serial].join('|');

  // hexdigest[:16] из Python == первые 8 байт дайджеста, прочитанные как big-endian число
  function digestToBigint(digest, bytes) {
    let num = 0n;
    for (const b of digest.slice(0, bytes)) num = (num << 8n) | BigInt(b);
    return num;
  }

  async function checksum(body, secret = null) {
    const msg = new TextEncoder().encode(body);
    const digest = secret ? await hmacSha256(secret, msg) : await sha256Bytes(msg);
    return toBase31(digestToBigint(digest, 4), CHECK_LEN);
  }

  // ---------- Публичный API ----------
  /**
   * Генерирует уникальный ID из пяти полей. Детерминирован: те же данные → тот же код.
   * @returns {Promise<{id:string, compact:string, canonical:string, fields:object}>}
   */
  async function makeId({ producer, date, location, company, serial }, secret = null) {
    const P = norm(producer), L = norm(location), C = norm(company), S = norm(serial);
    const missing = [['производитель', P], ['место', L], ['компания', C], ['серийный номер', S]]
      .filter(([, v]) => !v).map(([n]) => n);
    if (missing.length) throw new Error('Заполните поля: ' + missing.join(', '));
    const iso = normDate(date);

    const digest = await sha256Bytes(new TextEncoder().encode(canonicalString(P, iso, L, C, S)));
    const hashPart = toBase31(digestToBigint(digest, 8), HASH_LEN);

    const body = prefix(P) + prefix(C) + prefix(L) + hashPart;
    const full = body + await checksum(body, secret);
    const blocks = [];
    for (let i = 0; i < full.length; i += BLOCK) blocks.push(full.slice(i, i + BLOCK));

    return {
      id: blocks.join(SEP),
      compact: full,
      canonical: canonicalString(P, iso, L, C, S),
      fields: { producer: P, date: iso, location: L, company: C, serial: S },
    };
  }

  const normalizeId = (raw) =>
    String(raw ?? '').replace(/[\s\-_]+/g, '').toUpperCase().replace(/[^A-Z0-9]/g, '');

  /** Офлайн-проверка контрольного кода ID без доступа к базе (аналог проверки IMEI). */
  async function verifyChecksum(rawId, secret = null) {
    const compact = normalizeId(rawId);
    if (compact.length !== TOTAL_LEN) return false;
    if ([...compact].some(ch => !ALPHABET.includes(ch))) return false;
    const body = compact.slice(0, BODY_LEN), check = compact.slice(BODY_LEN);
    return (await checksum(body, secret)) === check;
  }

  /** Разбор ID на читаемые составляющие (префиксы, хеш, контрольный код). */
  function extractParts(rawId) {
    const c = normalizeId(rawId);
    return {
      producer_prefix: c.slice(0, 2),
      company_prefix: c.slice(2, 4),
      location_prefix: c.slice(4, 6),
      hash: c.slice(6, BODY_LEN),
      checksum: c.slice(BODY_LEN),
    };
  }

  return {
    ALPHABET, TOTAL_LEN, BODY_LEN,
    makeId, verifyChecksum, normalizeId, extractParts, checksum,
    canonicalString, toBase31, normDate,
  };
});
