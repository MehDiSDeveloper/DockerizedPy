// ==========================================================================
// چالش | app.js — shared icon set used across all Jinja-rendered templates
// ==========================================================================

// ---- icon set (inline SVG, stroke-based, consistent 24px grid) ----------
const icons = {
  home: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/></svg>`,
  list: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/></svg>`,
  plus: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>`,
  user: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c1.6-4 5-6 8-6s6.4 2 8 6"/></svg>`,
  bell: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 1112 0c0 5 2 6 2 6H4s2-1 2-6"/><path d="M10 20a2 2 0 004 0"/></svg>`,
  search: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>`,
  help: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M9.2 9.2a2.9 2.9 0 015.6.9c0 1.9-2.8 2.4-2.8 4"/><path d="M12 17.2h.01"/></svg>`,
  check: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>`,
  x: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>`,
  trash: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>`,
  clock: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>`,
  users: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.2"/><path d="M2.5 20c1-3.3 3.4-5 6.5-5s5.5 1.7 6.5 5"/><circle cx="17" cy="8" r="2.6"/><path d="M15 8.2A2.6 2.6 0 1119.6 9.7"/><path d="M15.5 15.2c2.6.2 4.5 1.8 5.3 4.8"/></svg>`,
  flame: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2c1 4-4 5-4 9a4 4 0 008 0c1.3 1 2 2.6 2 4.2A6.2 6.2 0 0112 22a6.2 6.2 0 01-6-6.4C6 10 12 8 12 2z"/></svg>`,
  seal: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="8"/><path d="M9 12l2 2 4-4"/></svg>`,
  empty: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="7" width="16" height="13" rx="2"/><path d="M8 7V5a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>`,
  mail: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>`,
  phone: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="2" width="12" height="20" rx="3"/><path d="M11 18.5h2"/></svg>`,
  message: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a8 8 0 01-8 8H7l-4 3 1-4.4A8 8 0 1121 12z"/><path d="M8.5 11.5h.01M12 11.5h.01M15.5 11.5h.01"/></svg>`,
  lock: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="10" width="16" height="10" rx="2"/><path d="M8 10V7a4 4 0 118 0v3"/></svg>`,
  eye: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/></svg>`,
  eyeOff: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l18 18"/><path d="M10.6 5.1A11 11 0 0123 12s-1.6 2.8-4.4 4.9M6.4 6.9C3.8 8.8 2 12 2 12s4 7 11 7c1.3 0 2.6-.2 3.7-.6"/><path d="M9.5 9.5a3 3 0 004.2 4.2"/></svg>`,
  chevronRight: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg>`,
  chevronLeft: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 6l-6 6 6 6"/></svg>`,
  // The end of a row that opens a sheet *upwards* rather than a page:
  // «chevronLeft» promises somewhere to go, this promises something to come up.
  chevronUp: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 15l6-6 6 6"/></svg>`,
  calendar: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 10h18"/></svg>`,
  filter: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5h16M7 12h10M10 19h4"/></svg>`,
  logout: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/></svg>`,
  edit: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>`,
  share: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.6 10.6l6.8-3.8M8.6 13.4l6.8 3.8"/></svg>`,
  // Duplicating a sheet: the "copy this to the clipboard" glyph. The group
  // management screen's links are copied, never opened, so the action needs a
  // mark of its own rather than borrowing `link` (which names the *thing*).
  copy: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="12" height="12" rx="2.4"/><path d="M5.5 15H5a2 2 0 01-2-2V5a2 2 0 012-2h8a2 2 0 012 2v.5"/></svg>`,
  // Two arrows chasing each other: replacing something with a fresh copy of
  // itself. Revoking the public link and minting the next one is one act, so
  // it gets one glyph -- `trash` would say the group loses its public address.
  refresh: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.5 11a8.5 8.5 0 00-14.6-5.1L3 8.8"/><path d="M3.5 13a8.5 8.5 0 0014.6 5.1L21 15.2"/><path d="M3 4.2v4.6h4.6M21 19.8v-4.6h-4.6"/></svg>`,
  target: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></svg>`,
  chart: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20V10M12 20V4M20 20v-7"/></svg>`,
  camera: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg>`,
  // A finger over a target: the one glyph in the set that means "tap this".
  // The tour's handover step is its only caller -- a step that waits for the
  // member's own tap has to say so in words *and* show it.
  tap: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11.5V6.2a1.8 1.8 0 013.6 0v7.6"/><path d="M12.6 12.1a1.7 1.7 0 013.4 0v.9"/><path d="M16 13.4a1.7 1.7 0 013.4 0v3.1a5 5 0 01-5 5h-2a4.6 4.6 0 01-3.6-1.7l-3-3.7a1.7 1.7 0 012.5-2.2l1.7 1.7"/></svg>`,
  // ---- challenge categories -------------------------------------------
  // One icon per ChallengeCategory member. The mapping from the (Farsi) enum
  // value to these names lives server-side in app/icons.py and reaches the
  // templates as the `category_icon` Jinja filter, so the Farsi strings are
  // never duplicated into JS -- except in the create wizard, which builds its
  // own pill grid client-side and keeps its list in sync by hand.
  catFitness: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 8v8M17.5 8v8"/><path d="M3.5 10.5v3M20.5 10.5v3"/><path d="M6.5 12h11"/></svg>`,
  catNutrition: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8.4c-1.2-1.1-2.7-1.5-4.1-1C5.9 8.2 5 10.2 5 12.6 5 16 7.3 20 9.6 20c.8 0 1.6-.4 2.4-.4s1.6.4 2.4.4c2.3 0 4.6-4 4.6-7.4 0-2.4-.9-4.4-2.9-5.2-1.4-.5-2.9-.1-4.1 1z"/><path d="M12 8.4V5.6"/><path d="M12 5.6c1.7 0 3-1.4 3-3.1-1.7 0-3 1.4-3 3.1z"/></svg>`,
  catMental: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5.6a3 3 0 00-5.5 1.6A2.9 2.9 0 004.2 10a2.9 2.9 0 001.5 2.6A3 3 0 007 17.6a2.9 2.9 0 005 1.2z"/><path d="M12 5.6a3 3 0 015.5 1.6A2.9 2.9 0 0119.8 10a2.9 2.9 0 01-1.5 2.6A3 3 0 0117 17.6a2.9 2.9 0 01-5 1.2z"/><path d="M12 5.6v13.2"/></svg>`,
  catProductivity: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13.2 2.5L4.8 13.2h5.6l-.8 8.3 8.6-11.2h-5.6z"/></svg>`,
  catSocial: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9.9l-.8-.8a2.2 2.2 0 10-3.1 3.1l3.9 3.9 3.9-3.9a2.2 2.2 0 10-3.1-3.1z"/><path d="M3.5 13.5l2.6 5.3A3 3 0 008.8 20.5h6.4a3 3 0 002.7-1.7l2.6-5.3"/></svg>`,
  catOther: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 3l1.6 4.3L17 9l-4.4 1.7L11 15l-1.6-4.3L5 9l4.4-1.7z"/><path d="M17.5 14.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/></svg>`,

  // ---- cadence kinds ----------------------------------------------------
  // One icon per CadenceKind member; see CADENCE_ICONS in app/icons.py.
  cadenceOnce: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 21V3.5"/><path d="M5.5 4.5h11l-2.2 3.6L16.5 12h-11"/></svg>`,
  cadenceSchedule: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 11.5V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2h5.5"/><path d="M8 2.5v4M16 2.5v4M3 9.5h17"/><circle cx="17.5" cy="17.5" r="4.5"/><path d="M17.5 15.6v2l1.4 1.2"/></svg>`,
  cadenceDays: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 2.5l3.2 3.2L17 8.9"/><path d="M3.8 11.7V9.7a4 4 0 014-4h12.4"/><path d="M7 21.5l-3.2-3.2L7 15.1"/><path d="M20.2 12.3v2a4 4 0 01-4 4H3.8"/></svg>`,
  cadenceQuota: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 18.5a8.5 8.5 0 1117 0"/><path d="M12 18.5l4.4-4.9"/><path d="M3.6 15.6l1.9.6M20.4 15.6l-1.9.6M12 10v2"/></svg>`,
  theme: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 000 18z" fill="currentColor" stroke="none"/></svg>`,
  settings: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3.2"/><path d="M19.4 14.5a1.6 1.6 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.6 1.6 0 00-1.8-.3 1.6 1.6 0 00-1 1.5v.2a2 2 0 11-4 0v-.1a1.6 1.6 0 00-1-1.5 1.6 1.6 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.6 1.6 0 00.3-1.8 1.6 1.6 0 00-1.5-1H3a2 2 0 110-4h.1a1.6 1.6 0 001.5-1 1.6 1.6 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.6 1.6 0 001.8.3h.1a1.6 1.6 0 001-1.5V3a2 2 0 114 0v.1a1.6 1.6 0 001 1.5 1.6 1.6 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.6 1.6 0 00-.3 1.8v.1a1.6 1.6 0 001.5 1h.2a2 2 0 110 4h-.1a1.6 1.6 0 00-1.5 1z"/></svg>`,
  // A domino mask -- anonymity, and deliberately not `eyeOff`, which the
  // visibility grid already spends on «فقط با لینک». One glyph, one meaning.
  mask: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 8.5c0 4.2-3.2 8-5.6 8-1.5 0-2.5-1.2-3.4-1.2s-1.9 1.2-3.4 1.2C6.2 16.5 3 12.7 3 8.5c0-1.6 1.1-2.6 3-2.6 1.6 0 2.9.6 4.1 1.4.7.5 1.1.7 1.9.7s1.2-.2 1.9-.7c1.2-.8 2.5-1.4 4.1-1.4 1.9 0 3 1 3 2.6z"/><path d="M7.3 10.2h2.2M14.5 10.2h2.2"/></svg>`,
  building: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 21V6a2 2 0 012-2h7a2 2 0 012 2v15"/><path d="M15 10h3a2 2 0 012 2v9"/><path d="M2 21h20"/><path d="M8 8h.01M11 8h.01M8 12h.01M11 12h.01M8 16h.01M11 16h.01M18 14h.01M18 17h.01"/></svg>`,
  school: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3L2 8l10 5 10-5z"/><path d="M6 10.5V16c0 1.7 2.7 3 6 3s6-1.3 6-3v-5.5"/><path d="M21 8.5V14"/></svg>`,
  link: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.5 13.5a4 4 0 005.7 0l3-3a4 4 0 10-5.7-5.7l-1.3 1.3"/><path d="M13.5 10.5a4 4 0 00-5.7 0l-3 3a4 4 0 105.7 5.7l1.3-1.3"/></svg>`,
  userPlus: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9.5" cy="8" r="3.6"/><path d="M2.5 20c1.1-3.6 3.7-5.5 7-5.5s5.9 1.9 7 5.5"/><path d="M18.5 6.5v6M21.5 9.5h-6"/></svg>`,
  shield: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5.5c0 4.4-2.9 8.2-7 9.5-4.1-1.3-7-5.1-7-9.5V6z"/><path d="M9.2 12.2l2 2 3.6-3.8"/></svg>`,
  info: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 8h.01"/></svg>`,
  reply: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 5 20 10 15 15"/><path d="M20 10H8a4 4 0 00-4 4v5"/></svg>`,
  smiley: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4.5 4.5 0 007 0"/><path d="M9 9.5h.01M15 9.5h.01"/></svg>`,
  // --- emoji keyboard tabs -------------------------------------------
  // One per group in `emoji.js`, plus `clock` for «اخیر». They are drawn on
  // the same 24px stroke grid as everything else on purpose: the tab strip
  // says *which drawer*, and a row of coloured emoji above a grid of
  // coloured emoji gives the eye nothing to land on.
  emSmiley: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4.5 4.5 0 007 0"/><path d="M9 9.5h.01M15 9.5h.01"/></svg>`,
  emPaw: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="6.5" cy="10" rx="2.1" ry="2.6"/><ellipse cx="10.6" cy="6.4" rx="2.1" ry="2.6"/><ellipse cx="15.4" cy="6.4" rx="2.1" ry="2.6"/><ellipse cx="19.5" cy="10" rx="2.1" ry="2.6"/><path d="M13 13.2c2.6 0 4.7 2 4.7 4.3 0 1.9-1.5 3-3.3 3-1 0-1.6-.4-2.4-.4s-1.4.4-2.4.4c-1.8 0-3.3-1.1-3.3-3 0-2.3 2.1-4.3 4.7-4.3z"/></svg>`,
  emFood: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 8.4c-1.2-1.1-2.7-1.5-4.1-1C5.9 8.2 5 10.2 5 12.6 5 16 7.3 20 9.6 20c.8 0 1.6-.4 2.4-.4s1.6.4 2.4.4c2.3 0 4.6-4 4.6-7.4 0-2.4-.9-4.4-2.9-5.2-1.4-.5-2.9-.1-4.1 1z"/><path d="M12 8.4V5.6"/><path d="M12 5.6c1.7 0 3-1.4 3-3.1-1.7 0-3 1.4-3 3.1z"/></svg>`,
  emCar: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 16.5v-3.2l1.8-4.6A2.5 2.5 0 017.1 7h9.8a2.5 2.5 0 012.3 1.7L21 13.3v3.2"/><path d="M3 13.4h18"/><circle cx="7" cy="17.2" r="1.8"/><circle cx="17" cy="17.2" r="1.8"/></svg>`,
  emBall: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7.2l4 2.9-1.5 4.7h-5L8 10.1z"/><path d="M12 3v4.2M4.5 9.4l3.5.7M19.5 9.4l-3.5.7M7.2 20l2.3-5.2M16.8 20l-2.3-5.2"/></svg>`,
  emBulb: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 17.5a6.5 6.5 0 116 0v1.7a1.5 1.5 0 01-1.5 1.5h-3A1.5 1.5 0 019 19.2z"/><path d="M9.5 17.5h5"/></svg>`,
  emSymbol: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.4 3.5L7.2 20.5M16.8 3.5l-2.2 17M4.4 8.9h15.2M3.6 15.1h15.2"/></svg>`,
  emFlag: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 21V3.5"/><path d="M5.5 4.5h11l-2.2 3.6L16.5 12h-11"/></svg>`,
  send: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 3L3 10.5l7 2.5 2.5 7z"/><path d="M21 3l-11 10"/></svg>`,
  heart: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20.3l-7.1-7a4.6 4.6 0 116.5-6.5l.6.6.6-.6a4.6 4.6 0 116.5 6.5z"/></svg>`,
  heartFill: `<svg viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20.3l-7.1-7a4.6 4.6 0 116.5-6.5l.6.6.6-.6a4.6 4.6 0 116.5 6.5z"/></svg>`,
  trophy: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 4h10v5a5 5 0 01-10 0z"/><path d="M7 5.5H4.8A1.8 1.8 0 003 7.3C3 9.6 4.8 11 7 11"/><path d="M17 5.5h2.2A1.8 1.8 0 0121 7.3c0 2.3-1.8 3.7-4 3.7"/><path d="M12 14v3.5"/><path d="M8.5 20.5h7"/><path d="M10 17.5h4l.7 3h-5.4z"/></svg>`,
};

// Injects inline SVGs into every [data-icon] placeholder under `root`.
// Exposed on window so pages can re-run it after inserting new markup
// (e.g. infinite-scroll fragments) that DOMContentLoaded never saw.
function renderIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((el) => {
    el.innerHTML = icons[el.dataset.icon] || "";
  });
}
window.renderIcons = renderIcons;

// ==========================================================================
// Dates and times
// ==========================================================================
// Storage stays Gregorian and UTC (see CLAUDE.md); this is a display layer
// only. The server renders ISO-8601 into a data attribute *and* as the
// element's text, and these helpers upgrade it in place to the Persian
// (Jalali) calendar. The ISO value survives in the attribute, so anything
// reading it back keeps working -- and if Intl has no Persian calendar, or JS
// never runs at all, the untouched ISO text is still a real date.
//
//   data-jalali="2026-07-04"                 -- a floating local calendar date
//   data-jalali="2026-07-04T14:30:00+00:00"  -- an absolute instant
//   data-jalali-format="day-month"           -- key into JALALI_FORMATS
//   data-jalali-tz="Asia/Tehran"             -- zone to resolve an instant in
//   data-jalali-prefix="هفتهٔ "                 -- literal text glued in front
//   data-jalali-attr="aria-label"            -- write here instead of textContent
//
// An instant must be rendered in the timezone it was *judged* in, not the
// viewer's: the occurrence engine derives "today" from each enrollment's own
// `timezone`, so a user abroad must still see check-in windows in the zone
// their streak is being scored against. Elements carrying an enrollment-scoped
// instant therefore pass data-jalali-tz explicitly. APP_TIMEZONE is only the
// fallback for challenge-level instants (due_date, created_at) that have no
// enrollment behind them -- it mirrors DEFAULT_TIMEZONE in
// app/routers/challenge.py, which already formats created_at the same way.
const APP_TIMEZONE = "Asia/Tehran";

// hour12 is pinned off: fa-IR defaults to a 12-hour clock with ق.ظ/ب.ظ,
// which is not how Iranians read times.
const JALALI_FORMATS = {
  "weekday-day-month": { weekday: "long", day: "numeric", month: "long" },
  "day-month": { day: "numeric", month: "long" },
  "day-month-year": { day: "numeric", month: "long", year: "numeric" },
  month: { month: "long", year: "numeric" },
  // The month alone, for an axis that already establishes the year -- the
  // home dashboard's activity grid spans one season, so repeating «۱۴۰۵»
  // over every label is noise. Not affected by the year-first reassembly
  // below: with no year part there is nothing to reorder.
  "month-only": { month: "long" },
  numeric: { year: "numeric", month: "2-digit", day: "2-digit" },
  time: { hour: "2-digit", minute: "2-digit", hour12: false },
  datetime: {
    day: "numeric", month: "long",
    hour: "2-digit", minute: "2-digit", hour12: false,
  },
  // dateStyle rather than a weekday/day/month/year bag on purpose: CLDR's fa
  // *full* pattern is year-first ("۱۴۰۵ شهریور ۵, پنجشنبه"), which no Iranian
  // writes. dateStyle:"long" gives the idiomatic "۵ شهریور ۱۴۰۵ ساعت ۲۳:۳۰".
  "datetime-full": { dateStyle: "long", timeStyle: "short", hour12: false },
};

// querySelectorAll skips `root` itself, which would silently miss a fragment
// whose own top-level node carries the attribute.
function selectAll(root, selector) {
  const found = Array.from(root.querySelectorAll(selector));
  if (root instanceof Element && root.matches(selector)) found.unshift(root);
  return found;
}

const ISO_OFFSET_RE = /(Z|[+-]\d{2}:?\d{2})$/;

// A bare "YYYY-MM-DD" is a local calendar date (occurrence dates, quota period
// starts) with no instant behind it, so it must never be shifted into another
// zone. It is parsed at local noon rather than midnight because in zones that
// skip midnight on a DST jump, "T00:00:00" can land on the previous day.
//
// An instant with no offset is UTC, not the viewer's local time -- which is
// what `new Date()` would otherwise assume, silently moving the rendered day
// for anyone outside Tehran. Everything the app writes is UTC (see CLAUDE.md);
// it arrives offset-less only because SQLite has no aware datetime type, so
// dev/test runs hand back "2026-10-06T11:06:36" where Postgres appends +00:00.
function parseDateValue(raw) {
  const value = String(raw || "").trim();
  if (!value) return null;
  const isInstant = value.includes("T");
  let text = `${value}T12:00:00`;
  if (isInstant) text = ISO_OFFSET_RE.test(value) ? value : `${value}Z`;
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : { date, isInstant };
}

// Returns null (rather than a garbled string) whenever it cannot format, so
// every caller can fall back to leaving the server's ISO text alone.
function formatJalali(raw, { format, timeZone } = {}) {
  const parsed = parseDateValue(raw);
  if (!parsed) return null;
  const opts = { ...(JALALI_FORMATS[format] || JALALI_FORMATS["day-month"]) };
  if (parsed.isInstant) opts.timeZone = timeZone || APP_TIMEZONE;
  try {
    const fmt = new Intl.DateTimeFormat("fa-IR-u-ca-persian", opts);
    // CLDR's fa year+month skeleton is also year-first ("۱۴۰۵ شهریور") and,
    // unlike the full date, has no dateStyle to fall back on -- so this one
    // label is reassembled from its parts.
    if (format === "month") {
      const parts = Object.fromEntries(
        fmt.formatToParts(parsed.date).map((p) => [p.type, p.value])
      );
      return `${parts.month} ${parts.year}`;
    }
    return fmt.format(parsed.date);
  } catch {
    return null; // no Persian calendar, or an unknown IANA zone
  }
}
window.formatJalali = formatJalali;

function renderJalaliDates(root = document) {
  selectAll(root, "[data-jalali]").forEach((el) => {
    const text = formatJalali(el.dataset.jalali, {
      format: el.dataset.jalaliFormat,
      timeZone: el.dataset.jalaliTz,
    });
    if (text === null) return;
    const out = (el.dataset.jalaliPrefix || "") + text;
    if (el.dataset.jalaliAttr) el.setAttribute(el.dataset.jalaliAttr, out);
    else el.textContent = out;
  });
}
window.renderJalaliDates = renderJalaliDates;

// ---- deadlines -----------------------------------------------------------
// A due occurrence carries absolute instants, but a bare clock time ("23:59")
// answers the wrong question -- what the user wants to know is how much room
// is left. The server supplies the fixed half of the sentence, since it is the
// only side that knows the cadence ("تا پایان این هفته"), and this fills in the
// live remainder beside it. The exact instant stays reachable through
// <time datetime> and a formatted title tooltip.
const RELATIVE_UNITS = [
  ["day", 86400],
  ["hour", 3600],
  ["minute", 60],
];
const URGENT_WINDOW_MS = 3 * 60 * 60 * 1000;

function formatRemaining(target, now) {
  const diffSeconds = (target.getTime() - now.getTime()) / 1000;
  const abs = Math.abs(diffSeconds);
  if (abs < 60) {
    return diffSeconds >= 0 ? "کمتر از یک دقیقه" : "همین حالا";
  }
  try {
    const rtf = new Intl.RelativeTimeFormat("fa", { numeric: "auto" });
    for (const [unit, seconds] of RELATIVE_UNITS) {
      if (abs >= seconds) return rtf.format(Math.trunc(diffSeconds / seconds), unit);
    }
  } catch {
    /* no RelativeTimeFormat -- leave whatever the server rendered */
  }
  return null;
}

function renderDeadlines(root = document) {
  const now = new Date();
  selectAll(root, "[data-deadline]").forEach((el) => {
    const iso = el.dataset.deadline;
    const parsed = parseDateValue(iso); // same offset-less-means-UTC rule
    if (!parsed) return;
    const target = parsed.date;
    const remainingMs = target.getTime() - now.getTime();
    const text = formatRemaining(target, now);
    if (text !== null) el.textContent = text;
    el.classList.toggle("is-passed", remainingMs <= 0);
    el.classList.toggle("is-urgent", remainingMs > 0 && remainingMs < URGENT_WINDOW_MS);
    // `closes_at_utc` is an *exclusive* bound -- local midnight opening the
    // next day. Counting down to it is right, but printing it verbatim shows
    // "۶ شهریور ساعت ۰:۰۰" under a card that says "تا پایان امروز (۵ شهریور)".
    // The tooltip therefore names the last instant still inside the window.
    // A scheduled occurrence is a point in time, not a bound, so it is exempt.
    const tipAt = el.hasAttribute("data-deadline-exclusive")
      ? new Date(target.getTime() - 1).toISOString()
      : iso;
    const exact = formatJalali(tipAt, {
      format: "datetime-full",
      timeZone: el.dataset.deadlineTz,
    });
    if (exact) el.title = exact;
  });
}
window.renderDeadlines = renderDeadlines;

// One entry point, so callers inserting markup (infinite-scroll fragments)
// cannot localize half of it and forget the rest.
function renderDates(root = document) {
  renderJalaliDates(root);
  renderDeadlines(root);
}
window.renderDates = renderDates;

// ==========================================================================
// Navigation trail (breadcrumbs + back buttons)
// ==========================================================================
// Every page is a full server-rendered document, so nothing survives a
// navigation on its own -- the trail is kept in sessionStorage, which is
// per-tab and dies with it, exactly the lifetime a "where did I come from"
// trail should have.
//
// Each page declares itself through the [data-crumb-trail] element that
// layout.html renders:
//
//   data-crumb-label="چالش‌ها"   -- how this page names itself in the trail
//   data-crumb-root              -- a bottom-nav destination: clears the trail
//
// Roots reset rather than append because the bottom nav is a *switch*, not a
// step forward: arriving at "خانه" from a challenge page means starting over,
// and without the reset the trail would grow forever as the user tabbed
// around. Revisiting a page already in the trail truncates back to it, so a
// loop (list -> detail -> list) collapses instead of repeating.

const TRAIL_KEY = "challenge:nav-trail";
// Deep enough for list -> detail -> profile -> ..., short enough to stay on
// one line on a phone.
const TRAIL_MAX = 4;

function readTrail() {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(TRAIL_KEY) || "[]");
    return Array.isArray(parsed) ? parsed.filter((e) => e && e.path && e.label) : [];
  } catch {
    return [];
  }
}

function writeTrail(trail) {
  try {
    sessionStorage.setItem(TRAIL_KEY, JSON.stringify(trail));
  } catch {
    /* private mode / storage full -- breadcrumbs are an enhancement */
  }
}

// Folds the current page into the stored trail and returns the result.
function pushCurrentPage({ label, isRoot }) {
  const path = location.pathname + location.search;
  let trail = isRoot ? [] : readTrail();
  // Match on pathname only: the same list page with a different filter in the
  // query string is the same *step*, and should collapse rather than stack.
  const seen = trail.findIndex((e) => e.path.split("?")[0] === location.pathname);
  if (seen >= 0) trail = trail.slice(0, seen);
  trail.push({ path, label });
  if (trail.length > TRAIL_MAX) trail = trail.slice(trail.length - TRAIL_MAX);
  writeTrail(trail);
  return trail;
}

// The page one step back from `trail`'s last entry, or null when the visit
// started here. `trail` is always the trail *including* the current page.
function previousPage(trail = readTrail()) {
  return trail.length > 1 ? trail[trail.length - 2] : null;
}
window.previousPage = previousPage;

// The trail lives in the topbar and doubles as the page's title, so it is
// always rendered: the last entry is this page's name at title weight, and
// the entries before it -- if any -- sit above it as a small path back. A
// single entry is just "you are here", which is exactly what a header on a
// root page should say, so it renders the title line alone rather than
// hiding. The server already wrote that title line into the element, so this
// only ever *upgrades* the header; it never has to build it from nothing.
function renderBreadcrumbs(nav, trail) {
  if (!trail.length) return;
  const current = trail[trail.length - 1];
  const ancestors = trail.slice(0, -1);
  const path = ancestors
    .map((entry) => {
      const crumb = `<a class="crumb" href="${escapeHtml(entry.path)}">${escapeHtml(entry.label)}</a>`;
      // The separator points along the reading direction, which is leftwards
      // in RTL -- hence chevronLeft, not chevronRight. Every ancestor carries
      // one, including the last: it points down into the title beneath it.
      return crumb + `<span class="crumb-sep" aria-hidden="true" data-icon="chevronLeft"></span>`;
    })
    .join("");
  nav.innerHTML =
    (path ? `<span class="crumb-path">${path}</span>` : "") +
    `<span class="crumb current" aria-current="page">${escapeHtml(current.label)}</span>`;
  nav.hidden = false;
  renderIcons(nav);
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// Icon-only back controls. Each one ships a server-rendered fallback href so
// it is a working link before (and without) JS; this only upgrades it to the
// page the visitor actually came from. `data-back-optional` marks a back
// button that has no reason to exist at the start of a visit -- on a root
// page reached directly there is nothing behind it, so it hides itself
// instead of offering a fake "back".
function wireBackButtons(previous) {
  document.querySelectorAll("[data-back]").forEach((el) => {
    if (previous) {
      el.setAttribute("href", previous.path);
      el.setAttribute("title", `بازگشت به ${previous.label}`);
      el.setAttribute("aria-label", `بازگشت به ${previous.label}`);
    } else if (el.hasAttribute("data-back-optional")) {
      el.hidden = true;
    }
  });
}

function initNavTrail() {
  const nav = document.querySelector("[data-crumb-trail]");
  // A page that opts out of the trail entirely (the create wizard) never
  // enters it, so the stored trail's *last* entry is already the page behind
  // us -- there is no current-page entry to skip over.
  if (!nav) {
    const stored = readTrail();
    wireBackButtons(stored.length ? stored[stored.length - 1] : null);
    return;
  }
  // Push first: the trail has to include this page before "one step back"
  // means anything.
  const trail = pushCurrentPage({
    label: (nav.dataset.crumbLabel || document.title || "").trim(),
    isRoot: nav.hasAttribute("data-crumb-root"),
  });
  wireBackButtons(previousPage(trail));
  renderBreadcrumbs(nav, trail);
}


// Relative deadlines go stale just by sitting on screen. One shared minute
// tick keeps every card honest instead of each one owning a timer.
// ---- notification bell ---------------------------------------------------
// The bell lives in layout.html's header, so it is on nearly every page --
// which is exactly why its unread count is *not* server-rendered. Filling it
// in server-side would mean every view router in the app running one more
// COUNT and passing one more context key, and a route that forgot would show
// a silently wrong badge. One small request, made once the page is up,
// belongs to the shell rather than to each of its screens.
//
// Two digits is the cap: past that the number stops being information and
// starts being a shape, and a three-digit badge no longer fits the 40px
// control it sits on.
const BELL_MAX_COUNT = 99;

function initNotificationBell() {
  const bell = document.querySelector("[data-notif-bell]");
  const badge = bell && bell.querySelector("[data-notif-badge]");
  // No bell on a signed-out shell (layout.html renders it only for a member),
  // and no request without one: an anonymous visitor asking would get a 401
  // and nothing to do with it.
  if (!bell || !badge || !document.body.dataset.userId) return;

  // Deliberately not apiFetch(): that helper sends a 401 to the login page,
  // which is right for an action the member asked for and wrong for a
  // background count -- a stale cookie would bounce someone out of the page
  // they were reading. Every failure here is silent; the worst case is a
  // header with no badge, which is what it already looks like.
  fetch("/notifications/unread-count", { headers: { Accept: "application/json" } })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      const unread = data && Number(data.unread);
      if (!unread) return;
      badge.textContent = unread > BELL_MAX_COUNT ? `+${BELL_MAX_COUNT}` : unread;
      badge.hidden = false;
      // The label carries the count too: the badge is a visual mark, and a
      // screen reader announcing "notifications" alone would lose the one
      // thing it is there to say.
      bell.setAttribute("aria-label", `اعلان‌ها، ${unread} مورد خوانده‌نشده`);
    })
    .catch(() => {});
}

const DEADLINE_TICK_MS = 60 * 1000;

document.addEventListener("DOMContentLoaded", () => {
  renderIcons();
  renderDates();
  initNavTrail();
  initNotificationBell();
  setInterval(() => renderDeadlines(document), DEADLINE_TICK_MS);

  // Elements not wired up to a real feature yet (account settings, password
  // change, ...) get a clear "coming soon" toast instead of doing
  // nothing when clicked -- a dead, silent control is worse UX than an
  // honest "not built yet" message.
  document.querySelectorAll("[data-coming-soon]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      showToast(el.dataset.comingSoon || "این بخش به‌زودی اضافه می‌شود");
    });
  });

  // Make custom `role="button"` elements (e.g. non-<button> menu rows)
  // keyboard-activatable with Enter/Space, matching native button behavior.
  document.querySelectorAll('[role="button"][tabindex]').forEach((el) => {
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        el.click();
      }
    });
  });
});

// ---- small shared UI/network helpers used across pages ----------------

// Debounce: delays invoking `fn` until `wait` ms have passed since the
// last call. Used for the search inputs so we don't hit the server on
// every keystroke.
function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}
window.debounce = debounce;

// Lightweight, non-blocking toast for feedback that doesn't warrant an
// alert() dialog (e.g. "coming soon" stubs, "link copied").
function showToast(message) {
  let el = document.getElementById("appToast");
  if (!el) {
    el = document.createElement("div");
    el.id = "appToast";
    el.className = "toast";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.classList.add("visible");
  clearTimeout(el._hideTimer);
  el._hideTimer = setTimeout(() => el.classList.remove("visible"), 2400);
}
window.showToast = showToast;

// Generic infinite-scroll helper: observes `sentinel` and fetches
// successive pages from `buildUrl(offset, limit)` as it enters the
// viewport, appending the returned HTML fragment into `container`.
// The first page is expected to already be server-rendered, so callers
// pass in the initial offset/hasMore instead of triggering a fetch.
function createInfiniteScroller({
  container,
  sentinel,
  loadingEl,
  emptyEl,
  pageSize = 20,
  initialOffset = 0,
  initialHasMore = false,
  buildUrl,
  onEmpty,
  onAppend,
  loginRedirectUrl,
}) {
  let offset = initialOffset;
  let hasMore = initialHasMore;
  let loading = false;
  let requestToken = 0;

  async function fetchPage(reset) {
    // A reset (filter/search/tab changed) always supersedes whatever is
    // in flight -- it must never be silently dropped just because a
    // previous page request hasn't resolved yet. Only plain "load next
    // page" calls respect the loading/hasMore guards.
    if (!reset && (loading || !hasMore)) return;
    const token = ++requestToken;
    if (reset) {
      offset = 0;
      hasMore = true;
      container.innerHTML = "";
      if (emptyEl) emptyEl.hidden = true;
    }
    loading = true;
    if (loadingEl) loadingEl.hidden = false;
    try {
      const res = await fetch(buildUrl(offset, pageSize));
      if (token !== requestToken) return; // superseded by a newer filter/reset
      if (res.status === 401) {
        window.location.href =
          loginRedirectUrl ||
          `/views/auth/?next=${encodeURIComponent(window.location.pathname)}`;
        return;
      }
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const html = await res.text();
      // Reading the body is a second await -- a reset can land in between, and
      // appending here would splice a stale page into the fresh container and
      // push `offset` past rows that were never rendered.
      if (token !== requestToken) return;
      hasMore = res.headers.get("X-Has-More") === "true";
      const wrapper = document.createElement("div");
      wrapper.innerHTML = html;
      const fragmentCount = wrapper.children.length;
      const nodes = Array.from(wrapper.children);
      while (wrapper.firstChild) container.appendChild(wrapper.firstChild);
      renderIcons(container);
      renderDates(container);
      if (typeof onAppend === "function") onAppend(nodes);
      offset += fragmentCount;
      if (offset === 0 && fragmentCount === 0 && typeof onEmpty === "function") {
        onEmpty();
      }
    } catch (err) {
      console.error("Failed to load more items:", err);
      // A reset() empties the container before its request goes out, so a
      // failure here would otherwise leave a blank list with no explanation
      // and no way back -- keep hasMore on so the sentinel can retry.
      if (token === requestToken) {
        hasMore = true;
        showToast("بارگذاری انجام نشد. دوباره تلاش کن.");
      }
    } finally {
      // Only the newest request owns the loading flag. A superseded one
      // clearing it would let the observer start a duplicate page fetch
      // while the newest request is still in flight.
      if (token === requestToken) {
        loading = false;
        if (loadingEl) loadingEl.hidden = true;
      }
    }
  }

  const observer = new IntersectionObserver(
    (entries) => {
      if (entries[0].isIntersecting) fetchPage(false);
    },
    { rootMargin: "200px 0px" }
  );
  observer.observe(sentinel);

  return {
    reset: () => fetchPage(true),
    // For a caller whose own container scrolls (the challenge list's
    // full-screen reader): the sentinel is then behind a fixed overlay and
    // can never intersect, so it asks for the next page itself. The
    // loading/hasMore guards inside fetchPage still apply, so calling it on
    // every scroll frame is safe.
    loadMore: () => fetchPage(false),
    // Callers that drop a card from the DOM *and* from the server's result
    // set must say so, or `offset` stays one too high and the next page
    // silently skips a row.
    notifyRemoved: (n = 1) => {
      offset = Math.max(0, offset - n);
    },
    disconnect: () => observer.disconnect(),
  };
}
window.createInfiniteScroller = createInfiniteScroller;

// JSON fetch helper for new code (existing hand-rolled fetch+alert() call
// sites are left alone -- see CLAUDE.md). Redirects to login on 401 instead
// of leaving the caller to handle it, and always resolves to
// {ok, status, data} instead of throwing.
async function apiFetch(url, options = {}) {
  const opts = { ...options };
  opts.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
  }

  let res;
  try {
    res = await fetch(url, opts);
  } catch (err) {
    return { ok: false, status: 0, data: { detail: err.message } };
  }

  if (res.status === 401) {
    window.location.href = `/views/auth/?next=${encodeURIComponent(window.location.pathname)}`;
    return { ok: false, status: 401, data: null };
  }

  let data = null;
  try {
    data = await res.json();
  } catch {
    data = null;
  }
  return { ok: res.ok, status: res.status, data };
}
window.apiFetch = apiFetch;

// The first modal/dialog in this codebase (everything else uses native
// confirm()/alert()). A bottom sheet with a focus trap, Escape-to-close,
// and backdrop-click-to-close; never itself uses confirm()/alert().
// `fields`: [{name, label, type, required, maxlength, step, placeholder,
// value, rows, options}]. `type` may additionally be "textarea", "select" or
// "chips" (the last two take `options: [{value, label}]`, and a chip option
// may carry an `icon` name from the set above); `value` prefills, which is
// what makes this usable for editing an existing record and not just creating
// one. "chips" is a single-select rendered as the same pill row the filter
// pages use -- a native <select> would hide the whole option set behind a tap,
// which is the opposite of what a filter sheet is for.
// `onConfirm(values)` / `onSkip(values)` may return `false` to keep the
// sheet open (e.g. after a failed request); anything else closes it.
// `danger: {label, onClick}` adds a separate destructive action below the
// main ones -- kept visually apart so "delete" is never adjacent to "save".
function createSheet({
  title,
  fields = [],
  onConfirm,
  onSkip,
  confirmLabel = "تایید",
  skipLabel = "رد کردن",
  danger,
  // Markup from `explain()`, rendered beside the sheet's heading.
  explainHtml,
  // Fired exactly once when the sheet goes away, however it went -- confirm,
  // skip, ✕, Esc or the backdrop. A sheet that *asks a question* has to hear
  // about the dismissal too, or a caller awaiting an answer waits forever.
  onClose,
}) {
  const backdrop = document.createElement("div");
  backdrop.className = "sheet-backdrop";

  const sheet = document.createElement("div");
  sheet.className = "sheet";
  sheet.setAttribute("role", "dialog");
  sheet.setAttribute("aria-modal", "true");
  sheet.setAttribute("aria-label", title || "");

  const panel = document.createElement("div");
  panel.className = "sheet-panel glass";

  const head = document.createElement("div");
  head.className = "sheet-head";
  const heading = document.createElement("h3");
  heading.textContent = title || "";
  // `explainHtml` is the markup `explain()` renders server-side (see
  // app/explainers.py), handed in by the page. A sheet is where the app asks
  // its harder questions -- what a lock means, what a mode does -- and it has
  // no room for a paragraph, so it gets the same «؟» every screen has rather
  // than a hint line per field saying half of it.
  if (explainHtml) {
    const slot = document.createElement("span");
    slot.innerHTML = explainHtml;
    const dot = slot.firstElementChild;
    if (dot) heading.appendChild(dot);
  }
  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "icon-btn";
  closeBtn.setAttribute("aria-label", "بستن");
  closeBtn.dataset.icon = "x";
  head.appendChild(heading);
  head.appendChild(closeBtn);

  const form = document.createElement("form");
  form.className = "sheet-body";
  const inputs = {};
  // A chips or avatars field's selection lives on a hidden input (so
  // collectValues stays a plain read of .value), but the highlight can only
  // be painted after the generic `f.value` prefill below has run.
  const chipSyncers = [];
  fields.forEach((f) => {
    // A "note" is a field with nothing to fill in: one or two sentences the
    // reader has to have in front of them *before* they press the button,
    // which is a different job from a `hint` (one line under a control,
    // saying what picking it means). It exists because the sheets that ask
    // the app's irreversible questions -- leaving a group, giving up every
    // challenge in it -- have no input at all, and a `confirm()` cannot name
    // what is about to be lost. It contributes no value, so `collectValues`
    // never sees it.
    if (f.type === "note") {
      const note = document.createElement("p");
      note.className = "sheet-note";
      note.textContent = f.text;
      if (f.tone) note.classList.add(`is-${f.tone}`);
      form.appendChild(note);
      return;
    }
    const wrap = document.createElement("div");
    // chips and avatars are their own grid of controls, so they drop the
    // boxed input shell every other field type wears (see .field-chips /
    // .field-avatars in styles.css).
    wrap.className = f.type === "chips" || f.type === "avatars"
      ? `field field-${f.type}`
      : "field";
    const label = document.createElement("label");
    label.textContent = f.label + (f.required ? " *" : "");
    label.setAttribute("for", `sheet-${f.name}`);
    const fieldInput = document.createElement("div");
    fieldInput.className = "field-input";

    let input;
    if (f.type === "chips") {
      input = document.createElement("input");
      input.type = "hidden";
      const group = document.createElement("div");
      group.className = "chip-row sheet-chips";
      group.setAttribute("role", "radiogroup");
      group.setAttribute("aria-label", f.label);
      const chips = (f.options || []).map((o) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "chip";
        chip.setAttribute("role", "radio");
        chip.dataset.value = o.value;
        if (o.icon) {
          const glyph = document.createElement("span");
          glyph.dataset.icon = o.icon;
          chip.appendChild(glyph);
        }
        chip.append(o.label);
        chip.addEventListener("click", () => {
          input.value = o.value;
          paint();
        });
        group.appendChild(chip);
        return chip;
      });
      function paint() {
        chips.forEach((chip) => {
          const on = chip.dataset.value === input.value;
          chip.classList.toggle("active", on);
          chip.setAttribute("aria-checked", on ? "true" : "false");
        });
      }
      chipSyncers.push(paint);
      fieldInput.appendChild(group);
    } else if (f.type === "avatars") {
      // Same mechanics as "chips" -- a hidden input holds the value, the
      // visible grid only paints it -- but the options are pictures, so it
      // reuses the signup picker's .avatar-pick/.ap-item markup rather than
      // growing a second look for the same choice. Options are
      // {value, url, label}; "" is a real, selectable value meaning "no
      // pick", which is why clicking the selected tile clears it.
      input = document.createElement("input");
      input.type = "hidden";
      const group = document.createElement("div");
      group.className = "avatar-pick sheet-avatars";
      group.setAttribute("role", "radiogroup");
      group.setAttribute("aria-label", f.label);
      const tiles = (f.options || []).map((o) => {
        const tile = document.createElement("button");
        tile.type = "button";
        tile.className = "ap-item";
        tile.setAttribute("role", "radio");
        tile.dataset.value = o.value;
        if (o.label) tile.setAttribute("aria-label", o.label);
        const img = document.createElement("img");
        img.src = o.url;
        img.alt = "";
        img.loading = "lazy";
        img.width = 52;
        img.height = 52;
        tile.appendChild(img);
        tile.addEventListener("click", () => {
          input.value = input.value === o.value ? "" : o.value;
          paintAvatars();
        });
        group.appendChild(tile);
        return tile;
      });
      // The grid scrolls, and 40 tiles is well past one screen, so the first
      // paint (which runs before the sheet is in the DOM -- hence the rAF)
      // brings the current pick into view instead of opening on strangers.
      let firstPaint = true;
      function paintAvatars() {
        let selected = null;
        tiles.forEach((tile) => {
          const on = tile.dataset.value === input.value;
          if (on) selected = tile;
          tile.classList.toggle("selected", on);
          tile.setAttribute("aria-checked", on ? "true" : "false");
        });
        if (firstPaint && selected) {
          // scrollTop on the grid rather than scrollIntoView, which would
          // also scroll the sheet body and the page behind it.
          requestAnimationFrame(() => {
            group.scrollTop =
              selected.offsetTop - group.clientHeight / 2 + selected.offsetHeight / 2;
          });
        }
        firstPaint = false;
      }
      chipSyncers.push(paintAvatars);
      fieldInput.appendChild(group);
    } else if (f.type === "textarea") {
      input = document.createElement("textarea");
      if (f.rows) input.rows = f.rows;
    } else if (f.type === "select") {
      input = document.createElement("select");
      (f.options || []).forEach((o) => {
        const opt = document.createElement("option");
        opt.value = o.value;
        opt.textContent = o.label;
        input.appendChild(opt);
      });
    } else {
      input = document.createElement("input");
      input.type = f.type || "text";
      if (f.step) input.step = f.step;
    }
    input.id = `sheet-${f.name}`;
    input.name = f.name;
    if (f.placeholder) input.placeholder = f.placeholder;
    if (f.maxlength) input.maxLength = f.maxlength;
    if (f.required) input.required = true;
    if (f.value != null) input.value = f.value;
    fieldInput.appendChild(input);
    wrap.appendChild(label);
    wrap.appendChild(fieldInput);
    // One line under the control saying what picking it *means*. Same job as
    // a settings row's `.sr-hint`, and it reuses that type scale: a choice
    // whose consequence is not on screen is a choice made blind.
    if (f.hint) {
      const hint = document.createElement("p");
      hint.className = "field-note";
      hint.textContent = f.hint;
      wrap.appendChild(hint);
    }
    form.appendChild(wrap);
    inputs[f.name] = input;
  });

  chipSyncers.forEach((paint) => paint());

  const actions = document.createElement("div");
  actions.className = "sheet-actions";
  // A sheet with nothing to skip (an edit form) shouldn't grow a second
  // primary-looking button just because the default label exists.
  let skipBtn = null;
  if (typeof onSkip === "function") {
    skipBtn = document.createElement("button");
    skipBtn.type = "button";
    skipBtn.className = "cc-btn ghost";
    skipBtn.textContent = skipLabel;
    actions.appendChild(skipBtn);
  }
  const confirmBtn = document.createElement("button");
  confirmBtn.type = "button";
  confirmBtn.className = "cc-btn primary";
  confirmBtn.textContent = confirmLabel;
  actions.appendChild(confirmBtn);

  panel.appendChild(head);
  panel.appendChild(form);
  panel.appendChild(actions);

  if (danger) {
    const dangerWrap = document.createElement("div");
    dangerWrap.className = "sheet-danger";
    const dangerBtn = document.createElement("button");
    dangerBtn.type = "button";
    dangerBtn.className = "cc-btn danger";
    dangerBtn.textContent = danger.label;
    dangerBtn.addEventListener("click", async () => {
      const result = await danger.onClick();
      if (result !== false) close();
    });
    dangerWrap.appendChild(dangerBtn);
    panel.appendChild(dangerWrap);
  }

  sheet.appendChild(panel);

  document.body.appendChild(backdrop);
  document.body.appendChild(sheet);
  const previousOverflow = document.body.style.overflow;
  document.body.style.overflow = "hidden";

  const previouslyFocused = document.activeElement;

  function collectValues() {
    const values = {};
    for (const [name, el] of Object.entries(inputs)) values[name] = el.value;
    return values;
  }

  function close() {
    document.removeEventListener("keydown", onKeydown);
    backdrop.removeEventListener("click", close);
    backdrop.remove();
    sheet.remove();
    document.body.style.overflow = previousOverflow;
    if (previouslyFocused && typeof previouslyFocused.focus === "function") {
      previouslyFocused.focus();
    }
    if (typeof onClose === "function") onClose();
  }

  function focusableEls() {
    return Array.from(
      panel.querySelectorAll(
        'button, input, textarea, select, [href], [tabindex]:not([tabindex="-1"])'
      )
    ).filter((el) => !el.disabled);
  }

  function onKeydown(e) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key !== "Tab") return;
    const focusables = focusableEls();
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  backdrop.addEventListener("click", close);
  closeBtn.addEventListener("click", close);
  document.addEventListener("keydown", onKeydown);

  if (skipBtn) {
    skipBtn.addEventListener("click", async () => {
      const result = await onSkip(collectValues());
      if (result !== false) close();
    });
  }

  confirmBtn.addEventListener("click", async () => {
    if (!form.reportValidity()) return;
    const result = typeof onConfirm === "function" ? await onConfirm(collectValues()) : true;
    if (result !== false) close();
  });

  renderIcons(sheet);
  const firstFocusable = focusableEls()[0];
  if (firstFocusable) firstFocusable.focus();

  return { close };
}
window.createSheet = createSheet;


// ==========================================================================
// Theme — «شن و مریم‌گلی» (light) / «شب کرمی» (dark)
//
// Three states, not two: "system" is the default and leaves the choice to
// the device, so styles.css's prefers-color-scheme block decides; "light"
// and "dark" pin it by writing [data-theme] on <html>, which the stylesheet
// weights above the media query in both directions.
//
// The value is read a second time by the inline guard in the <head> of
// layout.html / auth.html / error.html -- that copy runs before first paint,
// this one owns writing it. Keep the storage key in sync across the four.
// ==========================================================================
const THEME_KEY = "chalesh-theme";
const THEME_MODES = ["system", "light", "dark"];
const THEME_LABELS = { system: "پیش‌فرض دستگاه", light: "روشن", dark: "تاریک" };
const THEME_CONTROLS = "[data-theme-toggle], [data-theme-choice]";

function getTheme() {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    return THEME_MODES.includes(stored) ? stored : "system";
  } catch (e) {
    return "system";
  }
}

function setTheme(mode) {
  const next = THEME_MODES.includes(mode) ? mode : "system";
  if (next === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = next;
  try {
    if (next === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, next);
  } catch (e) {
    // private mode: the theme still applies, it just won't survive the page
  }
  document.querySelectorAll(THEME_CONTROLS).forEach(syncThemeControl);
  return next;
}

function cycleTheme() {
  return setTheme(THEME_MODES[(THEME_MODES.indexOf(getTheme()) + 1) % THEME_MODES.length]);
}

// A control is repainted, never read: the stored mode is the single source of
// truth, and no server can know it, so every control starts blank in the
// markup and gets its state from here on load.
function syncThemeControl(el) {
  const mode = getTheme();
  const out = el.querySelector("[data-theme-value]");
  if (out) out.textContent = THEME_LABELS[mode];
  el.querySelectorAll("[data-theme-option]").forEach(btn => {
    const on = btn.dataset.themeOption === mode;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-checked", on ? "true" : "false");
  });
}

// Two shapes over the same three states. `[data-theme-choice]` shows all
// three at once and is what the settings page uses -- a preference screen
// should answer "what are my options" without being poked. `[data-theme-toggle]`
// is the compact one-line form that cycles; nothing ships it today, but it is
// the shape a menu row needs and costs one branch to keep working.
function initThemeControls(root = document) {
  root.querySelectorAll(THEME_CONTROLS).forEach(el => {
    syncThemeControl(el);

    el.querySelectorAll("[data-theme-option]").forEach(btn => {
      btn.addEventListener("click", () => setTheme(btn.dataset.themeOption));
    });

    if (!el.matches("[data-theme-toggle]")) return;
    el.addEventListener("click", () => cycleTheme());
    el.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); cycleTheme(); }
    });
  });
}

document.addEventListener("DOMContentLoaded", () => initThemeControls());

/* ---- notification preferences (settings page) --------------------------
   One switch per NotificationKind, each saving on the tap: there is nothing
   to review before committing a boolean, so a «ذخیره» button would only add
   a state where the screen and the account disagree.

   The switch is optimistic -- it moves immediately, because a control that
   waits for a round trip before responding reads as broken -- but it owns
   the failure: on a rejected or dropped request it snaps back to what it
   was and says so. The one thing it must never do is look saved when it is
   not. `apiFetch` (not plain fetch) on purpose: this is a deliberate write,
   so a stale session should land on the login page rather than fail quietly.

   The rows are rendered by the server from the same map that words the
   notifications, so there is no list of kinds here to keep in step. */
function initNotificationPrefs(root = document) {
  root.querySelectorAll("[data-notif-pref]").forEach(input => {
    input.addEventListener("change", async () => {
      const wrap = input.closest(".switch");
      const wanted = input.checked;
      input.disabled = true;
      if (wrap) wrap.classList.add("is-saving");

      const { ok } = await apiFetch("/notifications/prefs", {
        method: "PUT",
        body: { kind: input.dataset.notifPref, enabled: wanted },
      });

      input.disabled = false;
      if (wrap) wrap.classList.remove("is-saving");
      if (!ok) {
        input.checked = !wanted;
        showToast("ذخیره نشد. دوباره تلاش کن.");
        return;
      }
      showToast(wanted ? "این اعلان روشن شد" : "این اعلان خاموش شد");
    });
  });
}

document.addEventListener("DOMContentLoaded", () => initNotificationPrefs());

window.getTheme = getTheme;
window.setTheme = setTheme;
window.cycleTheme = cycleTheme;


// ==========================================================================
// Press feedback — every tappable thing sinks under the finger and springs back
//
// One delegated listener rather than a class per control: the animation is
// pure CSS (see "Press feedback" in styles.css, which owns the scale, the
// spring and the lit state); this half only decides *which* element is being
// pressed and for how long.
//
// It is not `:active`, for three reasons that all show up on a phone:
//   - iOS Safari never applies `:active` to a plain element unless the page
//     carries a touch listener, so half the app would stay dead on the one
//     device it is designed for;
//   - `:active` survives a gesture that turns into a scroll, so every flick
//     down a card list would light a card on the way past;
//   - a quick tap holds it for a single frame, which reads as a flicker.
// So: the squeeze is held for PRESS_MIN_MS even on a flick tap, and it is
// cancelled the moment the pointer travels far enough to be a scroll.
//
// New markup needs nothing — the listener is on the document, so infinite
// scroll fragments and sheet contents built after load are covered.
// Opt in something that is none of the roles below with [data-press]; opt a
// subtree out with [data-no-press].
// ==========================================================================
const PRESS_MIN_MS = 110;   // minimum time the squeeze stays down
const PRESS_OUT_MS = 340;   // must match press-out's duration in styles.css
const PRESS_SLOP = 12;      // px of travel that reclassifies a tap as a scroll

const PRESS_SELECTOR = [
  "button:not([disabled])",
  "a[href]",
  "summary",
  '[role="button"]',
  '[role="tab"]',
  '[role="radio"]',
  '[role="option"]',
  "label[for]",
  "[data-press]",
].join(",");

let pressEl = null;
let pressStartedAt = 0;
let pressX = 0;
let pressY = 0;

function startPress(target, x, y) {
  const el = target && target.closest ? target.closest(PRESS_SELECTOR) : null;
  if (!el) return;
  if (el.hasAttribute("disabled") || el.getAttribute("aria-disabled") === "true") return;
  if (el.closest("[data-no-press]")) return;

  if (pressEl && pressEl !== el) endPress(true);
  pressEl = el;
  pressStartedAt = Date.now();
  pressX = x;
  pressY = y;

  clearTimeout(el._pressTimer);
  clearTimeout(el._releaseTimer);
  el.classList.remove("is-releasing");
  // A second tap landing mid-spring has to restart the animation, and the
  // browser only notices the class going away if the layout is read between.
  el.classList.remove("is-pressed");
  void el.offsetWidth;
  el.classList.add("is-pressed");
}

// `immediate` skips the minimum hold — used when the gesture was cancelled
// (a scroll, a lost pointer) rather than completed, where lingering on a
// control the finger has already left is exactly the wrong feedback.
function endPress(immediate) {
  const el = pressEl;
  if (!el) return;
  pressEl = null;

  const wait = immediate ? 0 : Math.max(0, PRESS_MIN_MS - (Date.now() - pressStartedAt));
  el._pressTimer = setTimeout(() => {
    el.classList.remove("is-pressed");
    el.classList.add("is-releasing");
    el._releaseTimer = setTimeout(() => el.classList.remove("is-releasing"), PRESS_OUT_MS);
  }, wait);
}

function initPressFeedback() {
  document.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;               // right/middle click is not a tap
    startPress(e.target, e.clientX, e.clientY);
  }, { passive: true });

  document.addEventListener("pointermove", (e) => {
    if (!pressEl) return;
    if (Math.hypot(e.clientX - pressX, e.clientY - pressY) > PRESS_SLOP) endPress(true);
  }, { passive: true });

  document.addEventListener("pointerup", () => endPress(false), { passive: true });
  document.addEventListener("pointercancel", () => endPress(true), { passive: true });
  window.addEventListener("blur", () => endPress(true));
  // capture, so a scroll inside a sheet or a card list cancels too — those
  // scroll events never reach window on their own.
  window.addEventListener("scroll", () => endPress(true), { capture: true, passive: true });

  // Keyboard activation gets the same beat, so a control reached with Tab
  // confirms the press the way a tap does.
  document.addEventListener("keydown", (e) => {
    if (e.repeat || (e.key !== "Enter" && e.key !== " ")) return;
    const el = document.activeElement;
    if (el && el.matches && el.matches(PRESS_SELECTOR)) startPress(el, 0, 0);
  });
  document.addEventListener("keyup", (e) => {
    if (e.key === "Enter" || e.key === " ") endPress(false);
  });
}

document.addEventListener("DOMContentLoaded", () => initPressFeedback());


// ==========================================================================
// راهنمای درجا — the in-place explainer
//
// The «؟» dots are rendered server-side by `explain()` in app/explainers.py,
// which is where every word of the glossary lives; this half only opens them.
//
// Three things are deliberate:
//   - **The text arrives in the markup**, in data attributes on the dot. No
//     fetch, no loading state, nothing to fail offline — and a dot appended by
//     an infinite-scroll fragment works the instant it lands, because the
//     listener below is delegated on the document and re-initialisation is
//     never needed.
//   - **One popover element, reused.** Only one explanation may be open at a
//     time: two open panes turn a quiet affordance into clutter, which is the
//     whole thing this design is avoiding.
//   - **Positioned fixed, from the dot's viewport rect.** A dot can sit inside
//     a scrolling card, a sticky header or a sheet, and an absolutely
//     positioned pane would be clipped by the first ancestor with overflow.
//     The trade is that the pane has to be re-placed while the page scrolls,
//     which is what the rAF-throttled reposition below does; it closes rather
//     than follows once its anchor has left the screen.
// ==========================================================================
const EXPLAIN_GAP = 10;   // dot to pane; must clear the arrow it grows
const EXPLAIN_EDGE = 12;  // smallest distance from the pane to a screen edge
const EXPLAIN_ARROW = 5.5;

let explainPop = null;    // the single reused pane
let explainDot = null;    // the dot it currently belongs to
let explainFrame = 0;

function explainPane() {
  if (explainPop) return explainPop;
  const pop = document.createElement("div");
  pop.className = "explain-pop";
  pop.setAttribute("role", "dialog");
  pop.tabIndex = -1;
  const arrow = document.createElement("i");
  arrow.className = "explain-arrow";
  pop.appendChild(arrow);
  document.body.appendChild(pop);
  explainPop = pop;
  return pop;
}

// Each point is "<name> — <what it means>": the name is emphasised and the
// meaning steps back, so a set of named states reads as a key rather than as
// a paragraph. A point without the dash is simply printed whole.
function explainPoint(text) {
  const li = document.createElement("li");
  const at = text.indexOf(" — ");
  if (at === -1) {
    li.textContent = text;
    return li;
  }
  const name = document.createElement("b");
  name.textContent = text.slice(0, at);
  li.appendChild(name);
  li.appendChild(document.createTextNode(text.slice(at)));
  return li;
}

function placeExplainer() {
  if (!explainPop || !explainDot) return;
  const dot = explainDot.getBoundingClientRect();

  // The anchor has scrolled away: a pane pointing at nothing is worse than a
  // pane that closed.
  if (dot.bottom < 0 || dot.top > window.innerHeight) {
    closeExplainer(false);
    return;
  }

  const pane = explainPop.getBoundingClientRect();
  // The bottom nav is fixed and always on screen, so the usable floor is
  // above it, not at the viewport edge -- a pane that "fits" over the nav is
  // one the reader has to move the phone to finish reading.
  const nav = document.querySelector(".bottom-nav");
  const floor = window.innerHeight - EXPLAIN_EDGE - (nav ? nav.offsetHeight : 0);
  const below = dot.bottom + EXPLAIN_GAP;
  const above = dot.top - EXPLAIN_GAP - pane.height;
  const fitsBelow = below + pane.height <= floor;
  const side = fitsBelow || above < EXPLAIN_EDGE ? "bottom" : "top";

  explainPop.dataset.side = side;
  explainPop.style.top = (side === "bottom" ? below : above) + "px";

  const centre = dot.left + dot.width / 2;
  const max = window.innerWidth - EXPLAIN_EDGE - pane.width;
  const left = Math.max(EXPLAIN_EDGE, Math.min(centre - pane.width / 2, max));
  explainPop.style.left = left + "px";

  // The arrow stays on the dot even when the pane itself was pushed off
  // centre by a screen edge — clamped so it never slides past the corner.
  const arrow = explainPop.querySelector(".explain-arrow");
  const arrowX = Math.max(14, Math.min(centre - left, pane.width - 14));
  arrow.style.left = arrowX - EXPLAIN_ARROW + "px";
}

function closeExplainer(returnFocus) {
  if (!explainDot) return;
  const dot = explainDot;
  explainDot = null;
  dot.setAttribute("aria-expanded", "false");
  if (explainPop) explainPop.classList.remove("is-open");
  if (returnFocus && typeof dot.focus === "function") dot.focus();
}

function openExplainer(dot) {
  const pop = explainPane();
  const title = dot.dataset.explainTitle || "";
  const body = dot.dataset.explainBody || "";
  const points = (dot.dataset.explainPoints || "").split("\n").filter(Boolean);

  // Rebuilt rather than patched: the pane is shared, so anything left over
  // from the previous entry would be a second explanation of the wrong thing.
  pop.querySelectorAll("h4, p, ul").forEach((el) => el.remove());
  const heading = document.createElement("h4");
  heading.textContent = title;
  pop.appendChild(heading);
  const para = document.createElement("p");
  para.textContent = body;
  pop.appendChild(para);
  if (points.length) {
    const list = document.createElement("ul");
    points.forEach((pt) => list.appendChild(explainPoint(pt)));
    pop.appendChild(list);
  }
  pop.setAttribute("aria-label", title);

  explainDot = dot;
  dot.setAttribute("aria-expanded", "true");
  // Made visible before measuring: the pane is `visibility:hidden` rather
  // than `display:none` precisely so it has a height to be placed by.
  pop.classList.add("is-open");
  placeExplainer();
  pop.focus();
}

function initExplainers() {
  document.addEventListener("click", (e) => {
    const dot = e.target.closest ? e.target.closest("[data-explain]") : null;
    if (dot) {
      e.preventDefault();
      // A second tap on the same dot puts it away — the dot is the control,
      // so it has to be able to undo itself.
      if (explainDot === dot) closeExplainer(true);
      else openExplainer(dot);
      return;
    }
    if (explainDot && !(explainPop && explainPop.contains(e.target))) {
      closeExplainer(false);
    }
  });

  // Capture, and it swallows the key: an explainer opened from inside a sheet
  // has to be the thing Esc puts away first, or one press closes both and the
  // member loses the form they were only asking a question about.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && explainDot) {
      e.preventDefault();
      e.stopPropagation();
      closeExplainer(true);
    }
  }, { capture: true });

  const replace = () => {
    if (!explainDot || explainFrame) return;
    explainFrame = requestAnimationFrame(() => {
      explainFrame = 0;
      placeExplainer();
    });
  };
  // capture, so a scroll inside a card list or a sheet is heard too — those
  // never reach window on their own.
  window.addEventListener("scroll", replace, { capture: true, passive: true });
  window.addEventListener("resize", replace, { passive: true });
}


// ---------------------------------------------------------------------------
// لایک
//
// One delegated listener for every like button on every page: a card list
// grows by infinite scroll, so a per-button listener would miss page two.
// The button carries its own subject (`data-like-subject` /
// `data-like-id`) rather than the page knowing what it is about, which is
// what keeps this generic across subject kinds — the route is one shape too.
//
// Optimistic, and it sends a *state* rather than a toggle: the server is
// told what should be true, so a retry or a second device cannot land on the
// opposite answer. A failure snaps back and says so, the same trade the
// notification switches make.
// ---------------------------------------------------------------------------
function paintLike(btn, count, liked) {
  btn.dataset.liked = liked ? "true" : "false";
  btn.classList.toggle("is-liked", liked);
  btn.setAttribute("aria-pressed", liked ? "true" : "false");
  btn.setAttribute("aria-label", liked ? "برداشتن لایک" : "لایک");
  const icon = btn.querySelector("[data-icon]");
  if (icon) {
    icon.setAttribute("data-icon", liked ? "heartFill" : "heart");
    renderIcons(btn);
  }
  const n = btn.querySelector("[data-like-count]");
  if (n) n.textContent = String(count);
}

function initLikes() {
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest ? e.target.closest("[data-like-subject]") : null;
    if (!btn) return;
    // Cards are a single `<a>`; without this a tap on the heart opens the
    // challenge instead of liking it.
    e.preventDefault();
    e.stopPropagation();
    if (btn.dataset.busy === "1") return;

    const wasLiked = btn.dataset.liked === "true";
    const el = btn.querySelector("[data-like-count]");
    const wasCount = Number(el ? el.textContent : 0) || 0;
    const liked = !wasLiked;

    btn.dataset.busy = "1";
    paintLike(btn, Math.max(0, wasCount + (liked ? 1 : -1)), liked);

    const url = `/reactions/${btn.dataset.likeSubject}/${btn.dataset.likeId}/likes`;
    const { ok, data } = await apiFetch(url, {
      method: "PUT",
      body: { liked },
    });
    btn.dataset.busy = "";

    if (ok && data) paintLike(btn, data.count, data.liked);
    else {
      paintLike(btn, wasCount, wasLiked);
      showToast("لایک ثبت نشد. دوباره تلاش کن.");
    }
  });
}

// ---------------------------------------------------------------------------
// صفحه‌کلید اموجی
//
// A keyboard, not a catalogue: what it produces is *characters typed into a
// textarea*, so nothing about an emoji reaches the server as an emoji — it
// arrives as part of the comment's text. That is the whole reason the set
// can be 1800 emoji instead of the 44 a stored id-per-sticker design could
// afford to keep in Python (see `app/stickers.py`, which still renders the
// rows written when it was one).
//
// The catalogue is `emoji.js` — a generated file, loaded only by the pages
// that carry a composer, so nothing here assumes it is present.
//
// Two mechanics are load-bearing:
//
//   * **One tab is in the DOM at a time.** Rendering all nine groups is
//     ~1800 buttons; rendering the open one is at most ~530, and the HTML of
//     each is cached after its first paint, so switching back is free.
//   * **The caret is remembered, not read at insert time.** Tapping a tile
//     may have taken focus off the textarea (and on a phone the keyboard is
//     deliberately dismissed when the panel opens), and an unfocused
//     textarea reports a caret at the end — which is exactly the bug where
//     every emoji lands after the text instead of where the writer was.
// ---------------------------------------------------------------------------
const EMOJI_RECENT_KEY = "chalesh-emoji-recent";
const EMOJI_RECENT_MAX = 32;

function readRecentEmoji() {
  // Same guard the theme reads with: storage throws outright in some
  // private-mode browsers, and a keyboard is not worth a broken page.
  try {
    const raw = JSON.parse(localStorage.getItem(EMOJI_RECENT_KEY) || "[]");
    return Array.isArray(raw) ? raw.filter((c) => typeof c === "string") : [];
  } catch (err) {
    return [];
  }
}

function pushRecentEmoji(char) {
  try {
    const next = [char, ...readRecentEmoji().filter((c) => c !== char)];
    localStorage.setItem(
      EMOJI_RECENT_KEY, JSON.stringify(next.slice(0, EMOJI_RECENT_MAX))
    );
  } catch (err) {
    /* a keyboard that cannot remember is still a keyboard */
  }
}

// The tiles' own font stack, repeated from styles.css because a measurement
// taken in a different font answers a different question.
const EMOJI_TILE_FONT =
  '23px "Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji","Twemoji Mozilla",sans-serif';

const EMOJI_NO_GLYPH = String.fromCodePoint(0x10fffd); // a character no font has
const EMOJI_BASELINE = String.fromCodePoint(0x1f600);  // E1.0 — every font has it

let emojiRuler = null;

/** The emoji of `list` this device can actually draw.
 *
 * A catalogue is a promise about Unicode, not about the reader's phone: an
 * emoji whose font predates it comes out as an empty box, and a ZWJ sequence
 * a font only half knows comes out as the two emoji it is made of. Both are
 * measurable — a box is exactly as wide as any other unknown glyph, and a
 * sequence that fell apart is nearly twice as wide as one emoji — so the
 * device decides what is on its own keyboard and nothing here has to guess a
 * version cap that would be wrong for somebody either way.
 *
 * It fails *open*: where the ruler cannot tell a box from an emoji (a font
 * that draws both at one width), the list comes back untouched — a keyboard
 * with a few boxes in it beats an empty one.
 */
function drawableEmoji(list) {
  try {
    if (!emojiRuler) {
      emojiRuler = document.createElement("canvas").getContext("2d");
      emojiRuler.font = EMOJI_TILE_FONT;
    }
    const box = emojiRuler.measureText(EMOJI_NO_GLYPH).width;
    const one = emojiRuler.measureText(EMOJI_BASELINE).width;
    if (!box || !one || Math.abs(box - one) < 0.5) return list;
    return list.filter((char) => {
      const w = emojiRuler.measureText(char).width;
      return w > box + 0.5 && w < one * 1.4;
    });
  } catch (err) {
    return list;
  }
}

function createEmojiPicker({ panel, tabsEl, labelEl, gridEl, emptyEl, onPick }) {
  const groups = Array.isArray(window.EMOJI_GROUPS) ? window.EMOJI_GROUPS : [];
  // «اخیر» is a tab like any other, and it is first because it is the one a
  // returning writer wants. It is empty on a first visit, which is the only
  // reason the panel needs an empty state at all.
  const tabs = [{ id: "recent", label: "اخیر", icon: "clock" }, ...groups];
  const cache = new Map();
  let active = null;

  tabsEl.innerHTML = tabs
    .map(
      (t) =>
        `<button type="button" class="em-tab" role="tab" data-em-tab="${t.id}"` +
        ` aria-selected="false" aria-label="${t.label}" title="${t.label}">` +
        `<span data-icon="${t.icon}"></span></button>`
    )
    .join("");
  renderIcons(tabsEl);

  const drawable = new Map();

  function charsFor(id) {
    // Recents are what this device already drew, so they need no filtering.
    if (id === "recent") return readRecentEmoji();
    if (!drawable.has(id)) {
      const group = groups.find((g) => g.id === id);
      drawable.set(id, group ? drawableEmoji(group.emoji) : []);
    }
    return drawable.get(id);
  }

  function show(id) {
    if (active === id) return;
    active = id;
    const tab = tabs.find((t) => t.id === id);
    labelEl.textContent = tab ? tab.label : "";
    tabsEl.querySelectorAll(".em-tab").forEach((el) => {
      const on = el.dataset.emTab === id;
      el.classList.toggle("is-active", on);
      el.setAttribute("aria-selected", on ? "true" : "false");
    });
    const chars = charsFor(id);
    // «اخیر» is rebuilt every time — it is the one tab whose contents change
    // while the panel is open.
    if (id === "recent" || !cache.has(id)) {
      const html = chars
        .map((c) => `<button type="button" class="em-tile" tabindex="-1">${c}</button>`)
        .join("");
      if (id !== "recent") cache.set(id, html);
      gridEl.innerHTML = html;
    } else {
      gridEl.innerHTML = cache.get(id);
    }
    if (emptyEl) emptyEl.hidden = chars.length > 0;
    gridEl.scrollTop = 0;
  }

  // Desktop: the panel must not steal focus, or the caret the textarea is
  // holding is gone before the tile's click handler runs.
  panel.addEventListener("mousedown", (e) => { e.preventDefault(); });

  tabsEl.addEventListener("click", (e) => {
    const tab = e.target.closest("[data-em-tab]");
    if (tab) show(tab.dataset.emTab);
  });

  gridEl.addEventListener("click", (e) => {
    const tile = e.target.closest(".em-tile");
    if (!tile) return;
    const char = tile.textContent;
    pushRecentEmoji(char);
    onPick(char);
  });

  function open() {
    panel.hidden = false;
    if (!active) show(readRecentEmoji().length ? "recent" : (groups[0] || {}).id);
    else if (active === "recent") { active = null; show("recent"); }
  }
  function close() { panel.hidden = true; }

  return {
    open,
    close,
    toggle() { if (panel.hidden) open(); else close(); },
    isOpen() { return !panel.hidden; },
  };
}
window.createEmojiPicker = createEmojiPicker;

document.addEventListener("DOMContentLoaded", () => {
  initExplainers();
  initLikes();
});
