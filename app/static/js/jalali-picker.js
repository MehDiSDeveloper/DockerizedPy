// ==========================================================================
// چالش | jalali-picker.js — Jalali date / time / datetime picker
// ==========================================================================
// Native <input type="date"> and <input type="datetime-local"> render a
// Gregorian calendar and there is no way to ask the browser for a Jalali one,
// so the whole app reads Persian dates everywhere except the one place where
// the user actually *types* one. This replaces those widgets.
//
// The native input is kept in the DOM as the single source of truth, only
// visually hidden: it still holds the same "YYYY-MM-DD" / "YYYY-MM-DDTHH:mm"
// value, still participates in the form, and still fires `input`/`change`.
// Everything already reading `.value` keeps working untouched -- this only
// changes how the value is *chosen* and *displayed*.
//
// Calendar maths comes from ICU (`Intl.DateTimeFormat("...-u-ca-persian")`),
// not a hand-rolled conversion: ICU is the same authority app.js formats with
// and app/jalali.py is tested against, so the picker can never disagree with
// the dates the rest of the UI shows.

(function () {
  "use strict";

  const DAY_MS = 86400000;
  const WEEKDAY_LABELS = ["ش", "ی", "د", "س", "چ", "پ", "ج"];
  const GRID_CELLS = 42; // 6 weeks -- a fixed height stops the sheet jumping

  // Latin digits so the parts can be parsed back as numbers.
  const partsFmt = new Intl.DateTimeFormat("en-u-ca-persian", {
    year: "numeric", month: "numeric", day: "numeric", timeZone: "UTC",
  });
  const monthNameFmt = new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
    month: "long", timeZone: "UTC",
  });
  // useGrouping off throughout: a year is not a quantity, and "۱٬۴۰۵" is wrong.
  const faNum = new Intl.NumberFormat("fa-IR", { useGrouping: false });
  const faNum2 = new Intl.NumberFormat("fa-IR", {
    minimumIntegerDigits: 2, useGrouping: false,
  });

  // Everything below works in UTC on purpose. A picker cell is a calendar day,
  // not an instant, and building it in local time makes days near a DST jump
  // land on their neighbour.
  const utc = (y, m, d) => new Date(Date.UTC(y, m - 1, d));

  function toJalali(date) {
    const p = Object.fromEntries(
      partsFmt.formatToParts(date).map((x) => [x.type, x.value])
    );
    return { jy: +p.year, jm: +p.month, jd: +p.day };
  }

  // 1 Farvardin is always 19-23 March of (jy + 621); asking ICU which one
  // beats reimplementing the 33-year leap cycle.
  const nowruzCache = new Map();
  function nowruz(jy) {
    if (nowruzCache.has(jy)) return nowruzCache.get(jy);
    for (let day = 19; day <= 23; day++) {
      const d = utc(jy + 621, 3, day);
      const p = toJalali(d);
      if (p.jy === jy && p.jm === 1 && p.jd === 1) {
        nowruzCache.set(jy, d);
        return d;
      }
    }
    throw new RangeError(`no Nowruz found for Jalali year ${jy}`);
  }

  function isLeapYear(jy) {
    return Math.round((nowruz(jy + 1) - nowruz(jy)) / DAY_MS) === 366;
  }

  function daysInMonth(jy, jm) {
    if (jm <= 6) return 31;
    if (jm <= 11) return 30;
    return isLeapYear(jy) ? 30 : 29;
  }

  function toGregorian(jy, jm, jd) {
    const beforeMonth = jm <= 7 ? (jm - 1) * 31 : 186 + (jm - 7) * 30;
    return new Date(nowruz(jy).getTime() + (beforeMonth + jd - 1) * DAY_MS);
  }

  // 0 = Saturday .. 6 = Friday, matching the Iranian indexing the rest of the
  // codebase uses (see to_ir_weekday in app/occurrences.py).
  const irWeekday = (date) => (date.getUTCDay() + 1) % 7;

  const monthName = (jy, jm) => monthNameFmt.format(toGregorian(jy, jm, 1));

  // Composed from two formatters rather than one weekday+day+month+year bag:
  // CLDR's fa *full* pattern is year-first ("۱۴۰۵ شهریور ۲۰, جمعه"), which no
  // Iranian writes. Same trap as JALALI_FORMATS["datetime-full"] in app.js.
  const weekdayFmt = new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
    weekday: "long", timeZone: "UTC",
  });
  const longDateFmt = new Intl.DateTimeFormat("fa-IR-u-ca-persian", {
    dateStyle: "long", timeZone: "UTC",
  });
  const longDate = (date) =>
    `${weekdayFmt.format(date)}، ${longDateFmt.format(date)}`;

  const isoDate = (date) =>
    `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}` +
    `-${String(date.getUTCDate()).padStart(2, "0")}`;

  function parseIsoDate(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value || "");
    return m ? utc(+m[1], +m[2], +m[3]) : null;
  }

  function parseIsoTime(value) {
    const m = /T(\d{2}):(\d{2})/.exec(value || "");
    return m ? { hour: +m[1], minute: +m[2] } : null;
  }

  // ---- the sheet ---------------------------------------------------------

  function openPicker({ input, withTime, onPick }) {
    const today = parseIsoDate(isoDate(new Date(Date.now() - new Date().getTimezoneOffset() * 60000)));
    const selectedDate = parseIsoDate(input.value) || null;
    const time = parseIsoTime(input.value) || { hour: 9, minute: 0 };
    const min = parseIsoDate(input.min);
    const max = parseIsoDate(input.max);

    let cursor = toJalali(selectedDate || today); // the month on screen
    let picked = selectedDate;

    const backdrop = document.createElement("div");
    backdrop.className = "sheet-backdrop";
    const shell = document.createElement("div");
    shell.className = "sheet";
    shell.innerHTML = `
      <div class="sheet-panel jp-panel" role="dialog" aria-modal="true" aria-label="انتخاب تاریخ">
        <div class="sheet-head">
          <h3 class="jp-heading"></h3>
          <button type="button" class="icon-btn jp-close" data-icon="x" aria-label="بستن"></button>
        </div>
        <div class="jp-nav">
          <button type="button" class="jp-nav-btn jp-prev-year" aria-label="سال قبل">
            <span data-icon="chevronRight"></span><span data-icon="chevronRight"></span>
          </button>
          <button type="button" class="jp-nav-btn jp-prev" data-icon="chevronRight" aria-label="ماه قبل"></button>
          <button type="button" class="jp-title" aria-live="polite"></button>
          <button type="button" class="jp-nav-btn jp-next" data-icon="chevronLeft" aria-label="ماه بعد"></button>
          <button type="button" class="jp-nav-btn jp-next-year" aria-label="سال بعد">
            <span data-icon="chevronLeft"></span><span data-icon="chevronLeft"></span>
          </button>
        </div>
        <div class="jp-weekdays">${WEEKDAY_LABELS.map(
          (w) => `<span>${w}</span>`
        ).join("")}</div>
        <div class="jp-grid" role="grid"></div>
        ${withTime ? `
        <div class="jp-time">
          <span data-icon="clock"></span>
          <label class="jp-time-label" for="jpHour">ساعت</label>
          <select id="jpHour" class="jp-select jp-hour"></select>
          <span class="jp-colon">:</span>
          <select class="jp-select jp-minute" aria-label="دقیقه"></select>
        </div>` : ""}
        <div class="sheet-actions jp-actions">
          <button type="button" class="cc-btn ghost jp-clear">پاک کردن</button>
          <button type="button" class="cc-btn ghost jp-today">امروز</button>
          <button type="button" class="cc-btn primary jp-confirm">تأیید</button>
        </div>
      </div>`;

    const panel = shell.querySelector(".jp-panel");
    const grid = shell.querySelector(".jp-grid");
    const title = shell.querySelector(".jp-title");
    const heading = shell.querySelector(".jp-heading");
    const hourSel = shell.querySelector(".jp-hour");
    const minuteSel = shell.querySelector(".jp-minute");

    if (withTime) {
      for (let h = 0; h < 24; h++) {
        hourSel.add(new Option(faNum2.format(h), String(h), false, h === time.hour));
      }
      for (let m = 0; m < 60; m++) {
        minuteSel.add(new Option(faNum2.format(m), String(m), false, m === time.minute));
      }
    }

    const beforeMin = (d) => min && d < min;
    const afterMax = (d) => max && d > max;

    function updateHeading() {
      heading.textContent = picked ? longDate(picked) : "انتخاب تاریخ";
    }

    function render() {
      title.textContent = `${monthName(cursor.jy, cursor.jm)} ${faNum.format(cursor.jy)}`;
      const first = toGregorian(cursor.jy, cursor.jm, 1);
      const lead = irWeekday(first);
      const total = daysInMonth(cursor.jy, cursor.jm);
      const todayIso = isoDate(today);
      const pickedIso = picked ? isoDate(picked) : null;

      const cells = [];
      for (let i = 0; i < GRID_CELLS; i++) {
        const dayNumber = i - lead + 1;
        if (dayNumber < 1 || dayNumber > total) {
          cells.push('<span class="jp-cell jp-blank" aria-hidden="true"></span>');
          continue;
        }
        const date = new Date(first.getTime() + (dayNumber - 1) * DAY_MS);
        const iso = isoDate(date);
        const disabled = beforeMin(date) || afterMax(date);
        const classes = [
          "jp-cell",
          iso === todayIso ? "is-today" : "",
          iso === pickedIso ? "is-selected" : "",
          irWeekday(date) === 6 ? "is-holiday" : "", // Friday
        ].filter(Boolean).join(" ");
        cells.push(
          `<button type="button" class="${classes}" data-iso="${iso}"` +
          `${disabled ? " disabled" : ""} aria-pressed="${iso === pickedIso}">` +
          `${faNum.format(dayNumber)}</button>`
        );
      }
      grid.innerHTML = cells.join("");
      updateHeading();
    }

    function shift(months, years) {
      let jm = cursor.jm + months;
      let jy = cursor.jy + years;
      while (jm > 12) { jm -= 12; jy += 1; }
      while (jm < 1) { jm += 12; jy -= 1; }
      try {
        nowruz(jy);
      } catch {
        return; // ran off the end of what ICU will answer for
      }
      cursor = { jy, jm, jd: 1 };
      render();
    }

    function close() {
      document.removeEventListener("keydown", onKeydown);
      backdrop.remove();
      shell.remove();
    }

    function onKeydown(e) {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
        return;
      }
      const step = { ArrowRight: -1, ArrowLeft: 1, ArrowUp: -7, ArrowDown: 7 }[e.key];
      if (step === undefined || !picked) return;
      e.preventDefault();
      const next = new Date(picked.getTime() + step * DAY_MS);
      if (beforeMin(next) || afterMax(next)) return;
      picked = next;
      cursor = toJalali(picked);
      render();
      const cell = grid.querySelector(".is-selected");
      if (cell) cell.focus();
    }

    grid.addEventListener("click", (e) => {
      const cell = e.target.closest(".jp-cell[data-iso]");
      if (!cell || cell.disabled) return;
      picked = parseIsoDate(cell.dataset.iso);
      render();
    });
    grid.addEventListener("dblclick", (e) => {
      if (e.target.closest(".jp-cell[data-iso]")) confirm();
    });

    shell.querySelector(".jp-prev").addEventListener("click", () => shift(-1, 0));
    shell.querySelector(".jp-next").addEventListener("click", () => shift(1, 0));
    shell.querySelector(".jp-prev-year").addEventListener("click", () => shift(0, -1));
    shell.querySelector(".jp-next-year").addEventListener("click", () => shift(0, 1));
    title.addEventListener("click", () => {
      cursor = toJalali(today);
      render();
    });
    shell.querySelector(".jp-close").addEventListener("click", close);
    backdrop.addEventListener("click", close);

    shell.querySelector(".jp-today").addEventListener("click", () => {
      if (beforeMin(today) || afterMax(today)) return;
      picked = today;
      cursor = toJalali(today);
      render();
    });
    shell.querySelector(".jp-clear").addEventListener("click", () => {
      onPick(null);
      close();
    });

    function confirm() {
      if (!picked) {
        onPick(null);
        close();
        return;
      }
      let value = isoDate(picked);
      if (withTime) {
        value += `T${String(+hourSel.value).padStart(2, "0")}` +
                 `:${String(+minuteSel.value).padStart(2, "0")}`;
      }
      onPick(value);
      close();
    }
    shell.querySelector(".jp-confirm").addEventListener("click", confirm);

    document.addEventListener("keydown", onKeydown);
    document.body.append(backdrop, shell);
    if (window.renderIcons) window.renderIcons(shell);
    render();
    (grid.querySelector(".is-selected") || grid.querySelector(".is-today") ||
      shell.querySelector(".jp-confirm")).focus();
  }

  // ---- enhancement -------------------------------------------------------

  function labelFor(input, withTime) {
    const date = parseIsoDate(input.value);
    if (!date) return null;
    const text = longDateFmt.format(date);
    if (!withTime) return text;
    const t = parseIsoTime(input.value);
    return t ? `${text} — ${faNum2.format(t.hour)}:${faNum2.format(t.minute)}` : text;
  }

  function enhance(input) {
    if (input.dataset.jpReady) return;
    const withTime = input.type === "datetime-local";
    input.dataset.jpReady = "1";
    input.classList.add("jp-native");
    // `type` is left alone so the value format, form behaviour and any
    // min/max attributes stay exactly what the rest of the page expects.
    input.tabIndex = -1;
    input.setAttribute("aria-hidden", "true");

    const trigger = document.createElement("button");
    trigger.type = "button";
    trigger.className = "jp-trigger";
    trigger.setAttribute("aria-haspopup", "dialog");
    const placeholder = withTime ? "انتخاب تاریخ و ساعت" : "انتخاب تاریخ";

    function sync() {
      const text = labelFor(input, withTime);
      trigger.textContent = text || placeholder;
      trigger.classList.toggle("is-empty", !text);
      trigger.setAttribute(
        "aria-label",
        text ? `${placeholder}؛ انتخاب فعلی: ${text}` : placeholder
      );
    }

    trigger.addEventListener("click", () => {
      openPicker({
        input,
        withTime,
        onPick: (value) => {
          input.value = value || "";
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.dispatchEvent(new Event("change", { bubbles: true }));
          sync();
        },
      });
    });
    // Anything that sets .value programmatically can fire `change` to refresh.
    input.addEventListener("change", sync);

    input.insertAdjacentElement("afterend", trigger);
    sync();
  }

  function enhanceJalaliInputs(root = document) {
    root
      .querySelectorAll('input[type="date"], input[type="datetime-local"]')
      .forEach(enhance);
  }

  window.enhanceJalaliInputs = enhanceJalaliInputs;

  // The month arithmetic above is the app's only Jalali calendar, so anything
  // else drawing a month grid borrows it rather than keeping a second copy --
  // two implementations of the leap cycle is exactly how a cell ends up under
  // the wrong month name. Deliberately just the maths: the picker's sheet, its
  // min/max and its native-input plumbing stay private to this file.
  window.jalaliCalendar = {
    toJalali, toGregorian, daysInMonth, monthName, irWeekday, isoDate,
    parseIsoDate, utc, WEEKDAY_LABELS, GRID_CELLS, DAY_MS,
    faNum: (n) => faNum.format(n),
  };

  document.addEventListener("DOMContentLoaded", () => enhanceJalaliInputs());
})();
