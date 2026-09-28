/**
 * app.js — логика веб-интерфейса генератора уникальных ID.
 *
 * Работает в двух режимах:
 *   1) с сервером (по умолчанию): POST /api/generate, GET /api/verify — с записью во внутреннюю базу;
 *   2) автономно (офлайн): если сервер недоступен, генерация и офлайн-проверка контрольного
 *      кода выполняются прямо в браузере через js/idgen.js (без записи в базу).
 */
'use strict';

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json();
  if (!r.ok) throw new Error(j.detail || 'Ошибка запроса');
  return j;
}

/* ---------------- Создание ID ---------------- */
$('#genForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const fields = Object.fromEntries(new FormData(e.target));
  const box = $('#genResult');
  box.style.display = 'block';
  box.textContent = '…';
  try {
    let rec, badge;
    try {
      const res = await api('/api/generate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fields),
      });
      rec = res.record;
      badge = `<span class="badge ${res.created ? 'b-ok' : 'b-err'}">${res.created ? 'создан новый' : 'уже существовал в базе'}</span>`;
      loadList();
    } catch (serverErr) {
      // Офлайн-режим: считаем ID локально (тот же алгоритм, что на сервере)
      if (serverErr instanceof TypeError) {
        rec = await IdGen.makeId(fields);
        badge = '<span class="badge b-ok">рассчитан локально (офлайн, без записи в базу)</span>';
      } else throw serverErr;
    }
    const short = rec.short || (rec.compact ? [rec.compact.slice(0,5), rec.compact.slice(5,7), rec.compact.slice(7,8), rec.compact.slice(8,9)].join('-') : '');
    box.innerHTML = `<div class="id-big">${esc(rec.id)}</div>
      <div style="text-align:center;font-size:1.05rem;color:var(--accent);letter-spacing:2px;margin-top:4px">${esc(short)}</div>
      <div style="text-align:center;margin-top:6px">${badge}</div>
      <div class="hint" style="text-align:center">Каноническая строка: ${esc(rec.canonical)}</div>`;
  } catch (err) {
    box.innerHTML = `<span class="err">${esc(err.message)}</span>`;
  }
});

/* ---------------- Проверка ID ---------------- */
$('#verForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const id = new FormData(e.target).get('id');
  const box = $('#verResult');
  box.style.display = 'block';
  box.textContent = '…';
  try {
    let html = '';
    try {
      const res = await api('/api/verify?id=' + encodeURIComponent(id));
      html += `Контрольный код: <span class="badge ${res.checksum_valid ? 'b-ok' : 'b-err'}">${res.checksum_valid ? '✔ верен' : '✘ не верен'}</span><br>`;
      html += `Присутствует в базе: <span class="badge ${res.found_in_db ? 'b-ok' : 'b-err'}">${res.found_in_db ? '✔ да' : '✘ нет'}</span><br>`;
      if (res.authentic && res.record) {
        html += `<span class="badge b-ok" style="margin:6px 0;display:inline-block">ПОДЛИННЫЙ ID</span><br>`;
        const r = res.record;
        html += `<table>
          <tr><th>Производитель</th><td>${esc(r.producer)}</td></tr>
          <tr><th>Дата</th><td>${esc(r.date)}</td></tr>
          <tr><th>Место</th><td>${esc(r.location)}</td></tr>
          <tr><th>Компания</th><td>${esc(r.company)}</td></tr>
          <tr><th>Серийный №</th><td>${esc(r.serial)}</td></tr>
          <tr><th>Порт</th><td>${esc(r.port)}</td></tr>
          <tr><th>Площадка №</th><td>${esc(r.site)}</td></tr>
          <tr><th>Выдан</th><td>${esc(r.created_at)} UTC</td></tr></table>`;
      } else if (res.checksum_valid && !res.found_in_db) {
        html += `<span class="err">Формат корректен, но в базе такой код отсутствует.</span>`;
      } else if (!res.checksum_valid) {
        html += `<span class="err">Код не проходит проверку — вероятно опечатка или подделка.</span>`;
      }
    } catch (serverErr) {
      // Офлайн-режим: только проверка контрольного кода (как CRC у серийного номера)
      if (serverErr instanceof TypeError) {
        const ok = await IdGen.verifyChecksum(id);
        html += `Контрольный код (офлайн, без базы): <span class="badge ${ok ? 'b-ok' : 'b-err'}">${ok ? '✔ верен' : '✘ не верен'}</span><br>`;
        if (ok) {
          const p = IdGen.extractParts(id);
          const dd = p.date_decoded;
          html += `<table>
            <tr><th>Код производителя</th><td>${esc(p.producer_prefix)}</td></tr>
            <tr><th>Дата (читается из кода)</th><td>${dd ? esc(`неделя ${dd.week_in_month}, ${dd.month_name.toLowerCase()}, ${dd.weekday_name}`) : '—'}</td></tr>
            <tr><th>Сокращение компании</th><td>${esc(p.company_abbr)}</td></tr>
            <tr><th>Порт</th><td>${esc(p.port_name || p.port)}</td></tr>
            <tr><th>Площадка</th><td>${esc(p.site_number != null ? '№' + p.site_number : '—')}</td></tr>
            <tr><th>Хеш-часть</th><td>${esc(p.hash)}</td></tr>
            <tr><th>Контрольный код</th><td>${esc(p.checksum)}</td></tr></table>`;
          html += `<div class="hint">Сервер недоступен — наличие кода во внутренней базе не проверено.</div>`;
        } else {
          html += `<span class="err">Код не проходит проверку — вероятно опечатка или подделка.</span>`;
        }
      } else throw serverErr;
    }
    box.innerHTML = html;
  } catch (err) {
    box.innerHTML = `<span class="err">${esc(err.message)}</span>`;
  }
});

/* ---------------- Список последних записей ---------------- */
async function loadList() {
  try {
    const rows = await api('/api/list?limit=15');
    $('#list').innerHTML = rows.length
      ? `<table><tr><th>ID</th><th>Серийный №</th><th>Порт/площадка</th><th>Компания</th></tr>` +
        rows.map((r) => `<tr><td style="font-family:Consolas,monospace">${esc(r.id)}</td><td>${esc(r.serial)}</td><td>${esc((r.port||'') + (r.site ? ' / ' + r.site : ''))}</td><td>${esc(r.company)}</td></tr>`).join('') +
        `</table>`
      : '<em style="color:#64748b">база пуста</em>';
  } catch (e) { /* сервер недоступен — тихо пропускаем */ }
}
loadList();
