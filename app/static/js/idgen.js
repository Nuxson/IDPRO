/**
 * idgen.js — JS-порт ядра генерации уникальных ID (работает в браузере и Node.js).
 *
 * Полностью совместим с Python-версией app/idgen.py: те же входные данные + тот же
 * справочник кодов → тот же код. Формат ID v6: PP DWC CC порт площадка(4) + хеш(5) + CRC(2)
 * = 19 символов, группировка XXXX-XXXX-XXXX-XXXX-XXX.
 *
 * Состав компактного кода:
 *   [0:2]   PP    — код производителя ИЗ СПРАВОЧНИКА config/producers.json
 *                   (браузер подгружает его через GET /api/codes; офлайн-режим
 *                   принимает готовый объект {Название: "КД"})
 *   [2:5]   DWC   — дата: D = неделя в месяце (дни 1-7 = 1 ...), W = месяц (B=Январь..Q=Декабрь),
 *                   C = день недели (A=Понедельник..F=Пятница, G=Воскресенье).
 *                   Дата НЕ вводится пользователем: фиксируется автоматически (сегодня)
 *                   при генерации и хранится во внутренней базе.
 *   [5:7]   CC    — сокращение компании ИЗ СПРАВОЧНИКА config/companies.json
 *   [7]     S     — порт: TN_A -> A, TN_B -> B, TN_C -> C
 *   [8:12]  SSSS  — номер площадки compact-алфавита, 4 символа (0..1 336 335): 6 -> '2228', 42 -> '223A'
 *   [12:17] HHHHH — хеш SHA-256 от канонической строки v6 (место + серийный и др.)
 *   [17:19] CC    — контрольный код HMAC-SHA256 (аналог CRC у серийных номеров / IMEI)
 *
 * Использование (браузер):
 *   await IdGen.loadCodes();            // загружает справочники с сервера
 *   const res = await IdGen.makeId({ producer:'Ромашка-Завод',
 *       location:'Москва', company:'Вектор-Телеком', serial:'SN-00123', port:'TN_A', site:'6' });
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
  const ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'; // base31: без неоднозначных 0/O, 1/I/L, U, Y
  const BASE = ALPHABET.length;                      // 31
  const ALNUM36 = '23456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'; // сжатые сегменты: без 0/O, 1/I/L
  const BASE36 = ALNUM36.length;                     // 34
  const SEP = '-';
  const GROUPS = [4, 4, 4, 4, 3];               // XXXX-XXXX-XXXX-XXXX-XXX
  const CHECK_LEN = 2;
  const HASH_LEN = 5;                                 // хеш: 5 символов compact-алфавита (~45 млн вариантов)
  const SITE_LEN = 4;                                 // площадка: 4 символа compact-алфавита
  const PREFIX_TOTAL = 2 + 3 + 2 + 1 + SITE_LEN;      // PP+DWC+CC+порт+площадка = 12
  const BODY_LEN = PREFIX_TOTAL + HASH_LEN;           // 17
  const TOTAL_LEN = BODY_LEN + CHECK_LEN;             // 19
  const FORMAT_VERSION = 'v6';

  const MONTH_CODES = 'BCDEFGHJKLMNPQ';               // B=Январь ... Q=Декабрь
  const WEEKDAY_CODES = 'ABCDEFG';                    // A=Понедельник ... F=Пятница, G=Вс
  let PORT_CODES = { TN_A: 'A', TN_B: 'B', TN_C: 'C' };  // заменяется из /api/codes (ports.json)

  // Ёмкость кода площадки (без перебора — проверка двусмысленности O(1), как в idgen.py):
  const SITE_CAPACITY = Math.pow(BASE36, SITE_LEN);   // 1 336 336
  const SITE_MAX = SITE_CAPACITY - 1;                 // 1 336 335
  function isSiteAmbiguous(num) {
    if (!(num >= 0 && num < SITE_CAPACITY)) throw new Error('Номер площадки вне ёмкости кода');
    const a = Math.floor(num / Math.pow(BASE36, 3));
    const b = Math.floor(num / Math.pow(BASE36, 2)) % BASE36;
    const c = Math.floor(num / BASE36) % BASE36;
    return a < 5 && MONTH_CODES.includes(ALNUM36[b]) && WEEKDAY_CODES.includes(ALNUM36[c]);
  }

  // Справочники кодов (config/producers.json и config/companies.json на сервере).
  // В браузере заполняются через loadCodes() (GET /api/codes); в Node/тестах —
  // setCodes({producers:{...}, companies:{...}}). Программа не содержит названий
  // брендов — соответствия «название -> код» задаёт только пользователь.
  let CODES = { producers: {}, companies: {} };

  function setCodes(codes) {
    CODES = {
      producers: Object.fromEntries(Object.entries(codes?.producers || {})
        .map(([k, v]) => [norm(k), String(v).toUpperCase()])),
      companies: Object.fromEntries(Object.entries(codes?.companies || {})
        .map(([k, v]) => [norm(k), String(v).toUpperCase()])),
    };
    const ports = codes?.ports;
    if (ports && Object.keys(ports).length) {
      PORT_CODES = Object.fromEntries(Object.entries(ports)
        .map(([k, v]) => [norm(k), String(v).toUpperCase()]));
    }
    return CODES;
  }

  async function loadCodes(url = '/api/codes') {
    const r = await fetch(url);
    if (!r.ok) throw new Error('Не удалось загрузить справочники кодов');
    return setCodes(await r.json());
  }


  // ---------- Хеш-примитивы: WebCrypto (основной путь), чистый JS (фолбэк для Node) ----------
  async function sha256Bytes(bytes) {
    if (typeof crypto !== 'undefined' && crypto.subtle) {
      const buf = await crypto.subtle.digest('SHA-256', bytes);
      return new Uint8Array(buf);
    }
    return _sha256Pure(bytes);
  }

  // Чистый JS SHA-256 (используется только если недоступен WebCrypto).
  // Проверенная реализация FIPS 180-4; совпадает с Node crypto и Python hashlib.
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
    if (KK.length !== 64) throw new Error('KK table must contain 64 constants');
    const rotr = (x, n) => ((x >>> n) | (x << (32 - n))) >>> 0;
    let h = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    const len = msg.length;
    const padded = new Uint8Array((((len + 8) >> 6) + 1) << 6);
    padded.set(msg);
    padded[len] = 0x80;
    const bits = len * 8;
    const view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, Math.floor(bits / 0x100000000), false);
    view.setUint32(padded.length - 4, bits >>> 0, false);
    const w = new Uint32Array(64);
    for (let off = 0; off < padded.length; off += 64) {
      for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4, false);
      for (let i = 16; i < 64; i++) {
        const x = w[i - 15], y = w[i - 2];
        const s0 = (rotr(x, 7) ^ rotr(x, 18) ^ (x >>> 3)) >>> 0;
        const s1 = (rotr(y, 17) ^ rotr(y, 19) ^ (y >>> 10)) >>> 0;
        w[i] = (((w[i - 16] + s0) >>> 0) + ((w[i - 7] + s1) >>> 0)) >>> 0;
      }
      let [a, b, c, d, e, f, g, hh] = h;
      for (let i = 0; i < 64; i++) {
        const S1 = (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) >>> 0;
        const ch = ((e & f) ^ (~e & g)) >>> 0;
        const t1 = ((((hh + S1) >>> 0) + ((ch + KK[i]) >>> 0)) + w[i]) >>> 0;
        const S0 = (rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) >>> 0;
        const maj = ((a & b) ^ (a & c) ^ (b & c)) >>> 0;
        const t2 = (S0 + maj) >>> 0;
        hh = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      const add = [a, b, c, d, e, f, g, hh];
      h = h.map((x, i) => (x + add[i]) >>> 0);
    }
    const out = new Uint8Array(32);
    const ov = new DataView(out.buffer);
    h.forEach((x, i) => ov.setUint32(i * 4, x, false));
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

  function producerCode(name) {
    const n = norm(name);
    const code = CODES.producers[n];
    if (!code) throw new Error(`Производитель «${name}» не найден в справочнике config/producers.json`);
    return code;
  }

  function companyAbbr(name) {
    const n = norm(name);
    const code = CODES.companies[n];
    if (!code) throw new Error(`Компания «${name}» не найдена в справочнике config/companies.json`);
    return code;
  }

  function portCode(port) {
    // Нормализация как в Python: регистр/пробелы/разделители не значимы (TN-A == TN_A)
    const key = norm(port).replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '');
    if (PORT_CODES[key]) return PORT_CODES[key];
    throw new Error(`Неизвестный порт: "${port}". Допустимые значения — из справочника config/ports.json.`);
  }

  function siteCode(site) {
    let s = norm(site).replace(/ /g, '');
    s = s.replace(/^(НОМЕР|NOMER|NO|#)/, '') || s;   // как в Python: re.sub(...) or s
    if (!/^\d+$/.test(s)) throw new Error(`Некорректный номер площадки: "${site}" (ожидается целое число)`);
    const num = Number(s);
    if (!Number.isSafeInteger(num) || num < 0 || num > SITE_MAX) {
      throw new Error(`Номер площадки вне диапазона 0..${SITE_MAX}: "${site}"`);
    }
    if (isSiteAmbiguous(num)) {
      throw new Error(`Номер площадки ${num} зарезервирован (код совпадает с шаблоном сегмента даты) — выберите соседнее значение`);
    }
    return toBase36(num, SITE_LEN);                   // 4 символа: 6 -> '2228', 42 -> '223A'
  }

  function decodeSite(codeN) {                        // обратный разбор: '2228' -> 6
    let n = 0n;
    for (const ch of String(codeN).toUpperCase()) {
      const idx = ALNUM36.indexOf(ch);
      if (idx < 0) throw new Error('Недопустимый символ кода площадки: ' + ch);
      n = n * BigInt(BASE36) + BigInt(idx);
    }
    return Number(n);
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

  // Представление числа в алфавите фиксированной длины (BigInt — точность как у Python int)
  function toBase(number, length, alphabet, base) {
    let n = BigInt(number);
    const out = [];
    for (let i = 0; i < length; i++) {
      out.push(alphabet[Number(n % BigInt(base))]);
      n /= BigInt(base);
    }
    return out.reverse().join('');
  }
  const toBase31 = (number, length) => toBase(number, length, ALPHABET, BASE);
  const toBase36 = (number, length) => toBase(number, length, ALNUM36, BASE36);
  const digestToBigint = (digest, bytes) => {
    let num = 0n;
    for (const b of digest.slice(0, bytes)) num = (num << 8n) | BigInt(b);
    return num;
  };

  const canonicalString = (producer, isoDate, location, company, serial, port, site) =>
    [FORMAT_VERSION, producer, isoDate, location, company, serial, port, site].join('|');


  async function checksum(body, secret = null) {
    const msg = new TextEncoder().encode(body);
    const digest = secret ? await hmacSha256(secret, msg) : await sha256Bytes(msg);
    return toBase36(digestToBigint(digest, 4), CHECK_LEN);
  }

  // ---------- Публичный API ----------
  /**
   * Генерирует уникальный ID из полей. Детерминирован: те же данные → тот же код.
   * Дата НЕ запрашивается: фиксируется автоматически (сегодня); параметр `date`
   * — служебный (тесты/воспроизведение), в UI не передаётся.
   * @returns {Promise<{id:string, compact:string, canonical:string, fields:object}>}
   */
  async function makeId({ producer, date, location, company, serial, port, site }, secret = null) {
    const P = norm(producer), L = norm(location), C = norm(company), S = norm(serial);
    const PT = norm(port), ST = norm(site).replace(/ /g, '');
    const missing = [['производитель', P], ['место', L], ['компания', C],
                     ['серийный номер', S], ['порт', PT], ['номер площадки', ST]]
      .filter(([, v]) => !v).map(([n]) => n);
    if (missing.length) throw new Error('Заполните поля: ' + missing.join(', '));
    const iso = date ? normDate(date) : localIsoToday();

    const digest = await sha256Bytes(new TextEncoder().encode(canonicalString(P, iso, L, C, S, PT, ST)));
    const hashPart = toBase36(digestToBigint(digest, 8), HASH_LEN);

    const sitePart = siteCode(ST);
    const prefixPart = producerCode(P) + dateSegment(iso) + companyAbbr(C) + portCode(PT) + sitePart;
    const body = prefixPart + hashPart;
    const full = body + await checksum(body, secret);
    const blocks = [];
    let pos = 0;
    for (const size of GROUPS) { blocks.push(full.slice(pos, pos + size)); pos += size; }

    return {
      id: blocks.join(SEP),
      compact: full,
      short: [prefixPart.slice(0, 5), prefixPart.slice(5, 7), prefixPart.slice(7, 8), String(decodeSite(sitePart))].join(SEP),
      canonical: canonicalString(P, iso, L, C, S, PT, ST),
      date_segment: body.slice(2, 5),
      fields: { producer: P, date: iso, location: L, company: C, serial: S, port: PT, site: ST },
    };
  }

  const normalizeId = (raw) =>
    String(raw ?? '').replace(/[\s\-_]+/g, '').toUpperCase().replace(/[^A-Z0-9]/g, '');

  // 'Сегодня' в локальном часовом поясе браузера (как date.today() в Python)
  function localIsoToday() {
    const t = new Date();
    return `${String(t.getFullYear()).padStart(4, '0')}-${String(t.getMonth() + 1).padStart(2, '0')}-${String(t.getDate()).padStart(2, '0')}`;
  }

  /** Офлайн-проверка контрольного кода ID без доступа к базе (аналог проверки IMEI). */
  async function verifyChecksum(rawId, secret = null) {
    const compact = normalizeId(rawId);
    if (compact.length !== TOTAL_LEN) return false;
    // Площадка/хеш/CRC — compact-алфавит:
    if ([...compact.slice(8)].some((ch) => !ALNUM36.includes(ch))) return false;
    // Читаемые глазом сегменты (производитель/дата/компания/порт) — base31:
    if (!ALPHABET.includes(compact[0]) || !ALPHABET.includes(compact[1])) return false;
    if (!'23456'.includes(compact[2])) return false;                 // неделя в месяце 1..5
    if (!MONTH_CODES.includes(compact[3])) return false;             // месяц
    if (!WEEKDAY_CODES.includes(compact[4])) return false;           // день недели
    if (!ALPHABET.includes(compact[5]) || !ALPHABET.includes(compact[6])) return false; // компания
    if (!Object.values(PORT_CODES).includes(compact[7])) return false; // порт из ports.json
    try {                                                            // площадка вне двусмысленных
      if (isSiteAmbiguous(decodeSite(compact.slice(8, PREFIX_TOTAL)))) return false;
    } catch (e) { return false; }
    const body = compact.slice(0, BODY_LEN), check = compact.slice(BODY_LEN);
    return (await checksum(body, secret)) === check;
  }

  const MONTH_NAMES = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
                       'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  const WEEKDAY_NAMES = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];

  /** Разбор ID на читаемые составляющие (формат v6). */
  function extractParts(rawId) {
    const c = normalizeId(rawId);
    const parts = {
      producer_prefix: c.slice(0, 2),
      date_segment: c.slice(2, 5),
      company_abbr: c.slice(5, 7),
      port: c.slice(7, 8),
      site_code: c.slice(8, PREFIX_TOTAL),
      hash: c.slice(PREFIX_TOTAL, BODY_LEN),
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
    try { parts.site_number = c.length >= PREFIX_TOTAL ? decodeSite(parts.site_code) : null; }
    catch (e) { parts.site_number = null; }
    return parts;
  }

  return {
    ALPHABET, TOTAL_LEN, BODY_LEN, MONTH_CODES, WEEKDAY_CODES, PORT_CODES, SITE_LEN,
    makeId, verifyChecksum, normalizeId, extractParts, checksum,
    canonicalString, toBase31, toBase36, normDate, localIsoToday,
    producerCode, companyAbbr, portCode, siteCode, decodeSite, dateSegment,
    setCodes, loadCodes, getCodes: () => CODES,
  };
});
