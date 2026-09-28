/**
 * idgen.js — JS-порт ядра генерации уникальных ID (работает в браузере и Node.js).
 *
 * Полностью совместим с Python-версией app/idgen.py: те же входные данные → тот же код.
 * Формат ID v3: PP DWC CC порт площадка + хеш(6) + CRC(2), блоки по 4 символа.
 *
 * Состав компактного кода:
 *   [0:2]  PP   — код производителя (транслит; Ericsson/Эрикссон -> ER)
 *   [2:5]  DWC  — дата: D = неделя в месяце (дни 1-7 = 1 ...), W = месяц (B=Январь..Q=Декабрь),
 *                C = день недели (A=Понедельник..F=Пятница, G=Воскресенье)
 *   [5:7]  CC   — сокращение компании по инициалам слов (Масштаб-Связь -> MS)
 *   [7]    S    — порт: TN_A -> A, TN_B -> B, TN_C -> C
 *   [8]    S    — номер площадки (base31: 1->'2', ..., 9->'A')
 *   [9:15]      — хеш SHA-256 от канонической строки v3 (место + серийный и др.)
 *   [15:17]     — контрольный код HMAC-SHA256 (аналог CRC у серийных номеров / IMEI)
 *
 * Использование (браузер):
 *   const res = await IdGen.makeId({ producer:'Ericsson', date:'07:08:2026',
 *       location:'Москва', company:'Масштаб-Связь', serial:'SN-00123', port:'TN_A', site:'6' });
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
  const PREFIX_TOTAL = 9;                             // PP + DWC + CC + порт + площадка
  const BODY_LEN = PREFIX_TOTAL + HASH_LEN;           // 15
  const TOTAL_LEN = BODY_LEN + CHECK_LEN;             // 17

  const MONTH_CODES = 'BCDEFGHJKLMNPQ';               // B=Январь ... Q=Декабрь
  const SITE_CODES = '23456789ABCDEFGHJ';             // площадка: 1->'2', ..., 9->'A', 14->'J'
  const WEEKDAY_CODES = 'ABCDEFG';                    // A=Понедельник ... F=Пятница, G=Вс
  const PORT_CODES = { TN_A: 'A', TN_B: 'B', TN_C: 'C' };
  const PRODUCER_ALIASES = {
    ERICSSON: 'ER', NOKIA: 'NO', SIEMENS: 'SI', HUAWEI: 'HW', SAMSUNG: 'SA', ROBOTECH: 'RQ',
  };
  const COMPANY_ALIASES = { 'МАСШТАБ-СВЯЗЬ': 'MS', 'MASHTAB-SVYAZ': 'MS', 'MASHATAB-SVYAZ': 'MS' };

  // Таблица транслита — идентична _TMAP из idgen.py
  const TMAP = {
    'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'Д': 'D', 'Е': 'E', 'Ё': 'E',
    'Ж': 'Z', 'З': 'Z', 'И': 'I', 'Й': 'I', 'К': 'K', 'Л': 'L', 'М': 'M',
    'Н': 'N', 'О': 'O', 'П': 'P', 'Р': 'R', 'С': 'S', 'Т': 'T', 'У': 'U',
    'Ф': 'F', 'Х': 'H', 'Ц': 'C', 'Ч': 'CH', 'Ш': 'SH', 'Щ': 'SCH', 'Ъ': '',
    'Ы': 'Y', 'Ь': '', 'Э': 'E', 'Ю': 'JU', 'Я': 'JA',
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
  const KK = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c3d,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];
  function _sha256Pure(msg) {
    const rotr = (x, n) => ((x >>> n) | (x << (32 - n))) >>> 0;
    if (KK.length !== 64) throw new Error('KK table must contain 64 constants');
    let h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    const len = msg.length;
    const total = ((((len + 8) >> 6) + 1) << 6);          // паддинг: 0x80 + 8 байт длины
    const withPad = new Uint8Array(total);
    withPad.set(msg); withPad[len] = 0x80;
    const bitsLo = (len * 8) >>> 0, bitsHi = Math.floor(len / 536870912) >>> 0;  // длина в битах (64 бит BE)
    withPad[total - 4] = (bitsHi >>> 24) & 255; withPad[total - 3] = (bitsHi >>> 16) & 255;
    withPad[total - 2] = (bitsHi >>> 8) & 255;  withPad[total - 1] = bitsHi & 255;
    withPad[total - 8] = (bitsLo >>> 24) & 255; withPad[total - 7] = (bitsLo >>> 16) & 255;
    withPad[total - 6] = (bitsLo >>> 8) & 255;  withPad[total - 5] = bitsLo & 255;
    const w = new Uint32Array(64);
    for (let off = 0; off < total; off += 64) {
      for (let i = 0; i < 16; i++) {
        w[i] = ((withPad[off + i * 4] << 24) | (withPad[off + i * 4 + 1] << 16) |
               (withPad[off + i * 4 + 2] << 8) | withPad[off + i * 4 + 3]) >>> 0;
      }
      for (let i = 16; i < 64; i++) {
        const s0 = (rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3)) >>> 0;
        const s1 = (rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10)) >>> 0;
        w[i] = (((w[i - 16] + s0) >>> 0) + ((w[i - 7] + s1) >>> 0)) >>> 0;
      }
      let [a, b, c, d, e, f, g, hh] = h;
      for (let i = 0; i < 64; i++) {
        const S1 = (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) >>> 0;
        const ch = ((e & f) ^ (~e & g)) >>> 0;
        const t1 = (((hh + S1) >>> 0) + (((ch + KK[i]) >>> 0) + w[i])) >>> 0;
        const S0 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) >>> 0;
        const maj = ((a & b) ^ (a & c) ^ (b & c)) >>> 0;
        const t2 = (S0 + maj) >>> 0;
        hh = g; g = f; f = e; e = ((d + t1) >>> 0); d = c; c = b; b = a; a = ((t1 + t2) >>> 0);
      }
      const add = [a, b, c, d, e, f, g, hh];
      h = h.map((x, i) => (x + add[i]) >>> 0);
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

  // Только символы алфавита base31 (как _readable в Python): Эрикссон -> ER
  const readable = (text) => [...translit(norm(text))].filter((ch) => ALPHABET.includes(ch)).join('');

  function producerCode(name) {
    const n = norm(name);
    if (PRODUCER_ALIASES[n]) return PRODUCER_ALIASES[n];
    const letters = readable(n);
    if (letters.length >= 2) return letters.slice(0, 2);
    // детерминированный запасной путь (в Python — sha256("v3p:"+n)[:4]);
    // синхронность с Python гарантируется для алиасов и читаемых имён
    throw new Error(`Не удалось построить читаемый код производителя: ${name}`);
  }

  function companyAbbr(name) {
    const n = norm(name);
    if (COMPANY_ALIASES[n]) return COMPANY_ALIASES[n];
    const tr = translit(n);
    const words = tr.split(/[\s\-]+/).filter(Boolean);
    const initials = words.map((w) => w[0]).join('');
    let out = [...initials].filter((ch) => ALPHABET.includes(ch)).join('');
    if (out.length < 2) {
      for (const ch of readable(n)) { if (!out.includes(ch)) out += ch; if (out.length >= 2) break; }
    }
    return (out.slice(0, 2) + 'QQ').slice(0, 2);
  }

  function portCode(port) {
    const key = norm(port).replace(/[^A-Z0-9]/g, '_');
    if (PORT_CODES[key]) return PORT_CODES[key];
    const compact = key.replace(/_/g, '');
    if (PORT_CODES[compact]) return PORT_CODES[compact];
    throw new Error(`Неизвестный порт: "${port}" (допустимо: TN_A, TN_B, TN_C)`);
  }

  function siteCode(site) {
    let s = norm(site).replace(/ /g, '');
    s = s.replace(/^(\d+)$/, '$1');                     // 'НОМЕР6' -> '' -> fallback ниже не нужен для чистых чисел
    s = s.replace(/^(НОМЕР|NOMER|NO|#)/, '');           // Python: re.sub(... ) or s
    if (!s) throw new Error(`Некорректный номер площадки: "${site}" (ожидается число 1..99)`);
    if (!/^\d+$/.test(s)) throw new Error(`Некорректный номер площадки: "${site}" (ожидается число 1..99)`);
    const num = Number(s);
    if (num < 1 || num > 99) throw new Error(`Номер площадки вне диапазона 1..99: "${site}"`);
    return ALPHABET[(num - 1) % BASE];
  }

  const weekOfMonth = (day) => Math.floor((day - 1) / 7) + 1;   // дни 1–7 = неделя 1

  function dateSegment(isoDate) {
    const d = new Date(isoDate + 'T00:00:00Z');
    const y = d.getUTCFullYear();
    if (y < 2020 || y > 2073) throw new Error(`Дата вне диапазона кодировки (2020–2073): ${isoDate}`);
    const isoDay = ((d.getUTCDay() + 6) % 7);                   // 0=Пн ... 6=Вс
    return ALPHABET[weekOfMonth(d.getUTCDate()) - 1] + MONTH_CODES[d.getUTCMonth()] + WEEKDAY_CODES[isoDay];
  }

  function normDate(value) {
    const v = norm(value).replace(/[/.:]/g, '-');
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

  const canonicalString = (producer, isoDate, location, company, serial, port, site) =>
    ['v3', producer, isoDate, location, company, serial, port, site].join('|');

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
  async function makeId({ producer, date, location, company, serial, port, site }, secret = null) {
    const P = norm(producer), L = norm(location), C = norm(company), S = norm(serial);
    const PT = norm(port), ST = norm(site).replace(/ /g, '');
    const missing = [['производитель', P], ['место', L], ['компания', C],
                     ['серийный номер', S], ['порт', PT], ['номер площадки', ST]]
      .filter(([, v]) => !v).map(([n]) => n);
    if (missing.length) throw new Error('Заполните поля: ' + missing.join(', '));
    const iso = normDate(date);

    const digest = await sha256Bytes(new TextEncoder().encode(canonicalString(P, iso, L, C, S, PT, ST)));
    const hashPart = toBase31(digestToBigint(digest, 8), HASH_LEN);

    const prefixPart = producerCode(P) + dateSegment(iso) + companyAbbr(C) + portCode(PT) + siteCode(ST);
    const body = prefixPart + hashPart;
    const full = body + await checksum(body, secret);
    const blocks = [];
    for (let i = 0; i < full.length; i += BLOCK) blocks.push(full.slice(i, i + BLOCK));

    return {
      id: blocks.join(SEP),
      compact: full,
      short: [prefixPart.slice(0, 5), prefixPart.slice(5, 7), prefixPart.slice(7, 8), prefixPart.slice(8, 9)].join(SEP),
      canonical: canonicalString(P, iso, L, C, S, PT, ST),
      date_segment: body.slice(2, 5),
      fields: { producer: P, date: iso, location: L, company: C, serial: S, port: PT, site: ST },
    };
  }

  const normalizeId = (raw) =>
    [...String(raw ?? '').replace(/[\s\-_]+/g, '').toUpperCase()].filter((ch) => ALPHABET.includes(ch)).join('');

  /** Офлайн-проверка контрольного кода ID без доступа к базе (аналог проверки IMEI). */
  async function verifyChecksum(rawId, secret = null) {
    const compact = normalizeId(rawId);
    if (compact.length !== TOTAL_LEN) return false;
    if ([...compact].some((ch) => !ALPHABET.includes(ch))) return false;
    if (!'23456'.includes(compact[2])) return false;                 // неделя в месяце 1..5
    if (!MONTH_CODES.includes(compact[3])) return false;             // месяц
    if (!WEEKDAY_CODES.includes(compact[4])) return false;           // день недели
    if (!'ABC'.includes(compact[7])) return false;                   // порт TN_A/TN_B/TN_C
    if (!SITE_CODES.includes(compact[8])) return false;              // номер площадки
    const body = compact.slice(0, BODY_LEN), check = compact.slice(BODY_LEN);
    return (await checksum(body, secret)) === check;
  }

  const MONTH_NAMES = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
                       'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  const WEEKDAY_NAMES = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];

  /** Разбор ID на читаемые составляющие (формат v3). */
  function extractParts(rawId) {
    const c = normalizeId(rawId);
    const parts = {
      producer_prefix: c.slice(0, 2),
      date_segment: c.slice(2, 5),
      company_abbr: c.slice(5, 7),
      port: c.slice(7, 8),
      site: c.slice(8, 9),
      hash: c.slice(9, BODY_LEN),
      checksum: c.slice(BODY_LEN),
    };
    if ('23456'.includes(parts.date_segment[0]) && MONTH_CODES.includes(parts.date_segment[1])
        && WEEKDAY_CODES.includes(parts.date_segment[2])) {
      parts.date_decoded = {
        week_in_month: ALPHABET.indexOf(parts.date_segment[0]) + 1,
        month: MONTH_CODES.indexOf(parts.date_segment[1]) + 1,
        month_name: MONTH_NAMES[MONTH_CODES.indexOf(parts.date_segment[1])],
        weekday: WEEKDAY_CODES.indexOf(parts.date_segment[2]) + 1,
        weekday_name: WEEKDAY_NAMES[WEEKDAY_CODES.indexOf(parts.date_segment[2])],
      };
    } else parts.date_decoded = null;
    parts.port_name = Object.keys(PORT_CODES).find((k) => PORT_CODES[k] === parts.port) || null;
    parts.site_number = ALPHABET.indexOf(parts.site) + 1 || null;   // однозначные номера читаются напрямую
    return parts;
  }

  return {
    ALPHABET, TOTAL_LEN, BODY_LEN, MONTH_CODES, WEEKDAY_CODES, PORT_CODES,
    makeId, verifyChecksum, normalizeId, extractParts, checksum,
    canonicalString, toBase31, normDate,
    producerCode, companyAbbr, portCode, siteCode, dateSegment,
  };
});
