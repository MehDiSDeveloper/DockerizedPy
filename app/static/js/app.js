// ==========================================================================
// چالش | app.js — mock data + view logic (MVP, no framework)
// Structure kept modular so real API calls can replace mockApi.* later.
// ==========================================================================

const mockApi = {
  currentUser: { name: "مهدی", streak: 4 },

  myChallenges: [
    {
      id: 1,
      title: "۳۰ روز بدون شکر",
      category: "سلامتی",
      description: "یک ماه کامل بدون قند افزوده؛ فقط قند طبیعی میوه‌ها مجازه.",
      status: "active", // active | completed
      progress: 62,
      daysLeft: 11,
      participants: 128,
      owner: true,
    },
    {
      id: 2,
      title: "چالش کتاب‌خوانی هفتگی",
      category: "یادگیری",
      description: "هر هفته یک کتاب رو شروع کن و حداقل ۵۰ صفحه بخون.",
      status: "active",
      progress: 30,
      daysLeft: 4,
      participants: 54,
      owner: false,
    },
    {
      id: 3,
      title: "پیاده‌روی روزانه ۱۰هزار قدم",
      category: "ورزش",
      description: "هر روز حداقل ده‌هزار قدم راه برو و ثبتش کن.",
      status: "completed",
      progress: 100,
      daysLeft: 0,
      participants: 342,
      owner: false,
    },
  ],

  exploreChallenges: [
    { id: 101, title: "۲۱ روز مدیتیشن صبحگاهی", category: "ذهن‌آگاهی", participants: 87, days: 21, enrolled: false },
    { id: 102, title: "چالش ترک شبکه‌های اجتماعی", category: "سبک زندگی", participants: 210, days: 14, enrolled: false },
    { id: 103, title: "۱۰۰ روز کدنویسی روزانه", category: "یادگیری", participants: 640, days: 100, enrolled: true },
    { id: 104, title: "چالش نوشیدن آب کافی", category: "سلامتی", participants: 95, days: 30, enrolled: false },
    { id: 105, title: "دویدن ۵ کیلومتر هفتگی", category: "ورزش", participants: 172, days: 60, enrolled: false },
  ],

  categories: ["همه", "سلامتی", "ورزش", "یادگیری", "ذهن‌آگاهی", "سبک زندگی"],
};

// ---- icon set (inline SVG, stroke-based, consistent 24px grid) ----------
const icons = {
  home: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/></svg>`,
  list: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/></svg>`,
  plus: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>`,
  user: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.6-4 5-6 8-6s6.4 2 8 6"/></svg>`,
  bell: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 1112 0c0 5 2 6 2 6H4s2-1 2-6"/><path d="M10 20a2 2 0 004 0"/></svg>`,
  search: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>`,
  check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>`,
  x: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>`,
  trash: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>`,
  clock: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>`,
  users: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M2.5 20c1-3.3 3.4-5 6.5-5s5.5 1.7 6.5 5"/><circle cx="17" cy="8" r="2.6"/><path d="M15 8.2A2.6 2.6 0 1119.6 9.7"/><path d="M15.5 15.2c2.6.2 4.5 1.8 5.3 4.8"/></svg>`,
  flame: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2c1 4-4 5-4 9a4 4 0 008 0c1.3 1 2 2.6 2 4.2A6.2 6.2 0 0112 22a6.2 6.2 0 01-6-6.4C6 10 12 8 12 2z"/></svg>`,
  seal: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8"/><path d="M9 12l2 2 4-4"/></svg>`,
  empty: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="7" width="16" height="13" rx="2"/><path d="M8 7V5a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>`,
  mail: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>`,
  lock: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 118 0v3"/></svg>`,
  eye: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>`,
  eyeOff: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l18 18"/><path d="M10.6 5.1A11 11 0 0123 12s-1.6 2.8-4.4 4.9M6.4 6.9C3.8 8.8 2 12 2 12s4 7 11 7c1.3 0 2.6-.2 3.7-.6"/><path d="M9.5 9.5a3 3 0 004.2 4.2"/></svg>`,
  chevronRight: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>`,
  chevronLeft: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg>`,
  calendar: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/></svg>`,
  filter: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16M7 12h10M10 19h4"/></svg>`,
  logout: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/></svg>`,
  edit: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>`,
  share: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 10.6l6.8-3.8M8.6 13.4l6.8 3.8"/></svg>`,
  target: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></svg>`,
  chart: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V10M12 20V4M20 20v-7"/></svg>`,
  camera: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg>`,
  info: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 8h.01"/></svg>`,
};

// ---- render helpers -------------------------------------------------------
function challengeCardHTML(c) {
  const isDone = c.status === "completed";
  return `
  <div class="challenge-card glass ${isDone ? "completed" : ""}" data-id="${c.id}" onclick="if(!event.target.closest('[data-action]')) window.location.href='challenge-detail.html'">
    ${isDone ? `<div class="seal"><span>تکمیل<br/>شده</span></div>` : ""}
    <div class="cc-top">
      <span class="cc-tag">${c.category}</span>
    </div>
    <h3 class="cc-title">${c.title}</h3>
    <p class="cc-desc">${c.description}</p>

    <div class="cc-meta">
      <span class="cc-meta-item">${icons.users}${c.participants} نفر</span>
      ${
        isDone
          ? `<span class="cc-meta-item">${icons.check}تکمیل شد</span>`
          : `<span class="cc-meta-item">${icons.clock}${c.daysLeft} روز مانده</span>`
      }
    </div>

    <div class="cc-progress"><i style="width:${c.progress}%"></i></div>

    <div class="cc-actions">
      ${
        isDone
          ? `<button class="cc-btn ghost" data-action="view">${icons.seal} مشاهده نتیجه</button>`
          : `<button class="cc-btn ghost" data-action="offroll">${icons.x} انصراف</button>`
      }
      ${
        c.owner
          ? `<button class="cc-btn danger" data-action="delete">${icons.trash} حذف چالش</button>`
          : ""
      }
    </div>
  </div>`;
}

function emptyStateHTML(label) {
  return `<div class="empty">${icons.empty}<p>${label}</p></div>`;
}

// ---- home view logic --------------------------------------------------
function renderHome(filter = "active") {
  const list = document.getElementById("challengeList");
  if (!list) return;

  const items = mockApi.myChallenges.filter((c) => c.status === filter);
  list.innerHTML = items.length
    ? items.map(challengeCardHTML).join("")
    : emptyStateHTML(
        filter === "active"
          ? "هنوز چالش فعالی نداری. یکی رو از فهرست چالش‌ها انتخاب کن یا خودت بساز."
          : "هنوز چالشی رو تکمیل نکردی."
      );

  attachCardActions();
}

function attachCardActions() {
  document.querySelectorAll(".challenge-card").forEach((card) => {
    const id = Number(card.dataset.id);
    card.querySelectorAll("[data-action]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const action = btn.dataset.action;
        if (action === "offroll") {
          if (confirm("از این چالش انصراف می‌دی؟")) {
            mockApi.myChallenges = mockApi.myChallenges.filter((c) => c.id !== id);
            renderHome(currentFilter);
          }
        } else if (action === "delete") {
          if (confirm("این چالش برای همه شرکت‌کننده‌ها حذف می‌شه. مطمئنی؟")) {
            mockApi.myChallenges = mockApi.myChallenges.filter((c) => c.id !== id);
            renderHome(currentFilter);
          }
        }
      });
    });
  });
}

let currentFilter = "active";

function initTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      currentFilter = tab.dataset.filter;
      renderHome(currentFilter);
    });
  });
}

function initStats() {
  const elActive = document.getElementById("statActive");
  if (!elActive) return;
  const active = mockApi.myChallenges.filter((c) => c.status === "active").length;
  const done = mockApi.myChallenges.filter((c) => c.status === "completed").length;
  const streak = mockApi.currentUser.streak;

  elActive.textContent = active;
  document.getElementById("statDone").textContent = done;
  document.getElementById("statStreak").textContent = streak;
}

// ---- explore view logic --------------------------------------------------
function exploreCardHTML(c) {
  return `
  <div class="challenge-card glass explore-card" data-id="${c.id}">
    <div class="explore-thumb">${icons.target}</div>
    <div class="explore-body">
      <h3>${c.title}</h3>
      <p>
        <span>${c.category}</span>
        <span>${icons.users}${c.participants}</span>
        <span>${icons.calendar}${c.days} روزه</span>
      </p>
    </div>
    <button class="enroll-btn ${c.enrolled ? "enrolled" : ""}" data-action="toggle-enroll">
      ${c.enrolled ? "عضو شدی" : "ثبت‌نام"}
    </button>
  </div>`;
}

let exploreFilter = "همه";
let exploreQuery = "";

function renderExplore() {
  const list = document.getElementById("exploreList");
  if (!list) return;

  const items = mockApi.exploreChallenges.filter((c) => {
    const matchCat = exploreFilter === "همه" || c.category === exploreFilter;
    const matchQuery = c.title.includes(exploreQuery);
    return matchCat && matchQuery;
  });

  list.innerHTML = items.length
    ? items.map(exploreCardHTML).join("")
    : emptyStateHTML("چالشی با این مشخصات پیدا نشد.");

  list.querySelectorAll("[data-action='toggle-enroll']").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const card = btn.closest(".challenge-card");
      const id = Number(card.dataset.id);
      const item = mockApi.exploreChallenges.find((c) => c.id === id);
      item.enrolled = !item.enrolled;
      renderExplore();
    });
  });
}

function initExplore() {
  const chipRow = document.getElementById("categoryChips");
  if (!chipRow) return;

  chipRow.innerHTML = mockApi.categories
    .map((cat) => `<button class="chip ${cat === exploreFilter ? "active" : ""}" data-cat="${cat}">${cat}</button>`)
    .join("");

  chipRow.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      exploreFilter = chip.dataset.cat;
      chipRow.querySelectorAll(".chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      renderExplore();
    });
  });

  const searchInput = document.getElementById("exploreSearch");
  searchInput.addEventListener("input", (e) => {
    exploreQuery = e.target.value;
    renderExplore();
  });

  renderExplore();
}

document.addEventListener("DOMContentLoaded", () => {
  // inject icons into static placeholders
  document.querySelectorAll("[data-icon]").forEach((el) => {
    el.innerHTML = icons[el.dataset.icon] || "";
  });

  initTabs();
  initStats();
  renderHome(currentFilter);
  initExplore();
});
