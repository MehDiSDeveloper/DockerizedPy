# UI test scenarios — چالش / Challenge Manager

A hand-off document for an agent driving the app in a real browser. Every item is
`Action → Expected`. An item passes only if the whole expected result is observed.

## Before you start

- Run the app locally: `uvicorn app.main:app --reload` (base URL `http://localhost:8000`).
- Seed data: `python -m app.scripts.generate_mock_data` (10 users / 30 challenges / 60 enrollments).
- **Use the local dev database only.** These scenarios create, join, leave and delete
  real rows, and `/checkins` writes are hard to undo.
- The UI is Farsi and RTL (`lang="fa" dir="rtl"`). Farsi strings quoted below are the
  exact strings to look for.
- Accounts: register your own from the UI (name ≥ 3 chars, password ≥ 8 chars). You need
  **two** accounts (A and B) for the ownership/visibility items, and at least one challenge
  per cadence kind: `once`, `schedule`, `recurring_days`, `recurring_quota`.
- A few items need a cadence whose occurrence is older than the 2-day backfill window —
  create it by enrolling and letting a `recurring_days` challenge sit, or by seeding an
  enrollment with an older `start_date` directly in the DB.

## How to report

For each item report `id | PASS / FAIL / BLOCKED` and, on anything but PASS, the URL, what
you saw, plus any console error or failing request (method, path, status, response body).
Do not fix the code — only observe and report.

## Scenarios

### 1. Auth & session — `/views/auth/`
**1.1** Logged out (or in a private window), open `/views/today/`.

- Expected: 303 redirect to `/views/auth/?next=/views/today/`; the login card renders.

**1.2** Click the ثبت‌نام tab, then the ورود tab.

- Expected: The form swaps each time, the clicked tab gets the active style, and the footer line flips between حساب نداری؟ and قبلاً ثبت‌نام کردی؟.

**1.3** Click the footer link (بساز / وارد شو).

- Expected: Same switch as the tabs; the tab highlight follows.

**1.4** Click the eye icon inside a password field, then click it again.

- Expected: First click reveals the plain text and swaps the icon to eyeOff; second click masks it again.

**1.5** Register with a 2-character name.

- Expected: Alert with the generic invalid message (422 from `POST /users/`); no account created, still on the page.

**1.6** Register with a 4-character password.

- Expected: Alert with the generic invalid message (422); no account created.

**1.7** Register with رمز عبور and تکرار رمز عبور different.

- Expected: Alert containing `Passwords do not match` (400); no account created.

**1.8** Register with a valid name / email / matching 8+ char password.

- Expected: Session cookie set and redirect to `next` (default `/views/home/`); the home hero greets you by the name you registered.

**1.9** Log out, then log in with the right email and a wrong password.

- Expected: Alert `خطا در ورود: Invalid email or password` (401); you stay on the auth page.

**1.10** Log in with correct credentials.

- Expected: Redirect to `next`; the protected pages now render instead of redirecting.

**1.11** Open `/views/auth/?next=https://example.com` and log in. Repeat with `?next=//example.com`.

- Expected: Both land on `/views/home/` — never on an external host (the open-redirect clamp).

**1.12** Logged out, open `/views/challenges/create`, then log in on the page you land on.

- Expected: Redirect to `/views/auth/?next=/views/challenges/create`, and after login you land back on the create wizard.

**1.13** While on `/views/today/`, delete the session cookie in devtools, then scroll to trigger a fragment fetch.

- Expected: The 401 sends you to `/views/auth/?next=/views/today/` — no silent failure and no blank list.


### 2. Shell & navigation — `every page`
**2.1** Open any page and inspect the root element.

- Expected: `<html lang="fa" dir="rtl">`; text and layout run right-to-left with no horizontally scrolling body.

**2.2** On each page run `document.querySelectorAll('[data-icon]:empty').length` in the console.

- Expected: `0` — every icon slot is filled with an inline SVG.

**2.3** Visit امروز, خانه, چالش‌ها and پروفایل in turn.

- Expected: The bottom nav item for the current page carries the active state; only one is active at a time.

**2.4** Logged in, click the پروفایل nav item; then repeat logged out.

- Expected: Logged in it goes to `/views/users/<your id>`; logged out it goes to `/views/auth/`.

**2.5** Click the bell icon in the top bar.

- Expected: Toast `بخش اعلان‌ها به‌زودی اضافه می‌شود`; the page does not navigate.

**2.6** Open `/`.

- Expected: Redirects to `/views/today/` (or to the login page if you are logged out).

**2.7** Tab to a coming-soon row on your profile and press Enter, then Space.

- Expected: Both keys fire the same toast as a mouse click (`role="button"` keyboard support).


### 3. Today feed — `/views/today/`
**3.1** Open the page with nothing due (a fresh account with no enrollments).

- Expected: Empty state `امروز چیزی برای ثبت نداری. یه نفس بکش 🎉` and the مانده تا امروز counter reads 0.

**3.2** Open the page with items due.

- Expected: The counter equals the number of cards; each card title links to `/views/challenges/<id>`.

**3.3** Look at a card from a `schedule` challenge.

- Expected: Window label reads `ساعت HH:MM` in the enrollment's own timezone (24-hour, no ق.ظ/ب.ظ), with a live remainder beside it.

**3.4** Look at cards from `recurring_days`, `recurring_quota` and `once` challenges.

- Expected: `تا پایان امروز`; `تا پایان این هفته` / `تا پایان این ماه` plus an `n از target` tag; `هر وقت آماده بودی` with no countdown at all.

**3.5** Hover the remaining-time element on a non-schedule card.

- Expected: Tooltip names the last instant *inside* the window (e.g. 23:59 today), not next-day midnight.

**3.6** Leave the page open for one minute with an item due in a few hours.

- Expected: The remainder re-renders on the minute tick; inside the last 3 hours it takes the urgent style, and once passed it takes the passed style.

**3.7** Click ثبت وضعیت on a challenge that has a goal unit, then on one that has none.

- Expected: With a unit the sheet shows a required numeric مقدار field labelled with that unit; without one there is no مقدار field. Both show the optional یادداشت and لینک عکس fields.

**3.8** In the sheet, press تکمیل شد.

- Expected: The card disappears, the counter drops by one, toast `ثبت شد ✅`, and the check-in is stored as `completed`.

**3.9** On another card, press رد کردم.

- Expected: Card disappears, toast `رد شد`, check-in stored as `skipped` (not `completed`).

**3.10** Press تکمیل شد with the required مقدار left empty.

- Expected: Native validation blocks it; the sheet stays open and no request is sent.

**3.11** Open the sheet and close it with Escape, then again with a backdrop click, then again with the X button.

- Expected: Each closes the sheet with no check-in recorded, restores page scrolling, and returns focus to the ثبت وضعیت button that opened it.

**3.12** Open the sheet and press Tab repeatedly past the last control (and Shift+Tab past the first).

- Expected: Focus cycles inside the sheet only — it never reaches the page behind it.

**3.13** Force a failing check-in (e.g. record the occurrence in another tab first, then submit an occurrence that is no longer writable).

- Expected: Toast `خطا: …` with the server's message; the sheet stays open and the card is not removed.

**3.14** Record the last remaining card.

- Expected: The empty state appears without a page reload.

**3.15** With more than 20 items due, scroll to the bottom.

- Expected: Next page appends with a spinner while loading; new cards have rendered icons *and* localized dates/countdowns — no raw ISO strings.

**3.16** Go offline and scroll to trigger the next page.

- Expected: Toast `بارگذاری انجام نشد. دوباره تلاش کن.`; after going back online, scrolling loads the same page correctly with no gap or duplicate.


### 4. Home — `/views/home/`
**4.1** Open the page.

- Expected: Hero greets you by name; چالش فعال and تکمیل‌شده stats match your enrollment counts; the active list is rendered.

**4.2** Click the تکمیل‌شده tab.

- Expected: List resets to completed enrollments, `aria-selected` moves to it; if none, empty text reads `هنوز چالشی رو تکمیل نکردی.`

**4.3** Click between فعال and تکمیل‌شده quickly several times.

- Expected: Only the last selected tab's cards are shown — no mixed or stale rows, no duplicates.

**4.4** Inspect a completed card.

- Expected: It shows the تکمیل شده seal and a مشاهده نتیجه link, and has no انصراف button.

**4.5** Click انصراف and cancel the confirm dialog.

- Expected: Nothing changes: card stays, stats unchanged, no request sent.

**4.6** Click انصراف and accept.

- Expected: Card is removed, چالش فعال drops by one, and the enrollment is gone after a reload.

**4.7** Unenroll from the last card in the list.

- Expected: The empty state appears with the message for the currently selected tab.

**4.8** Unenroll from a card, then scroll to load the next page.

- Expected: The next page starts where the list actually ends — no row is skipped.

**4.9** Click the + FAB.

- Expected: Navigates to `/views/challenges/create`.

**4.10** Check a card with a deadline.

- Expected: The مهلت date is shown in the Persian calendar, not as `YYYY-MM-DD`.


### 5. Discover / challenge list — `/views/challenges/`
**5.1** Click a category chip.

- Expected: List resets to that category, the chip becomes active with `aria-selected="true"`, and the URL gains `?category=<value>`.

**5.2** Type a few characters in جستجوی چالش.

- Expected: One request fires ~300 ms after you stop typing (not per keystroke); the list resets and the URL gains `?q=`.

**5.3** Search for something that cannot match.

- Expected: Empty text reads `چالشی با این مشخصات پیدا نشد.`

**5.4** Clear the search and the category on an empty database.

- Expected: Empty text reads `هنوز چالشی ثبت نشده.`

**5.5** Reload the page with `?category=…&q=…` in the URL.

- Expected: The server renders the same filtered list, the matching chip is active and the search box is pre-filled.

**5.6** Scroll through more than 20 results.

- Expected: Pages append in 7-day-velocity order with no duplicates and no missing rows.

**5.7** Browse the list logged out, then logged in as the owner.

- Expected: Logged out only `public` challenges appear; logged in you additionally see your own and enrolled `private`/`unlisted` ones.

**5.8** Paste more than 100 characters into the search box.

- Expected: The request is still accepted/handled — no 422 error page and no broken list.

**5.9** Click a result card.

- Expected: Opens that challenge's detail page.


### 6. Create wizard — `/views/challenges/create`
**6.1** Press مرحلهٔ بعد on step 1 with an empty title.

- Expected: Alert `لطفاً عنوان چالش رو وارد کن.`; you stay on step 1.

**6.2** Enter a 2-character title and press مرحلهٔ بعد.

- Expected: Alert `عنوان چالش باید حداقل ۳ حرف باشه.`; still on step 1.

**6.3** Move forward and back through the three steps.

- Expected: The stepper marks passed steps as done and the current one as active; entered values survive the back-and-forth.

**6.4** Select each of the four نوع تکرار options in turn.

- Expected: Only that cadence's fields are visible; choosing زمان‌بندی‌شده inserts one empty time row automatically.

**6.5** With زمان‌بندی‌شده selected, press افزودن زمان repeatedly, then delete rows.

- Expected: Rows stop being added at 20; the delete button never removes the final remaining row.

**6.6** Press مرحلهٔ بعد with all schedule times empty.

- Expected: Alert `حداقل یک زمان برای چالش زمان‌بندی‌شده لازمه.`

**6.7** Choose روزهای تکراری → روزهای هفته, select no day, press مرحلهٔ بعد.

- Expected: Alert `حداقل یک روز از هفته رو انتخاب کن.`; the weekday grid starts at شنبه (Iranian week order).

**6.8** Switch the recurring mode to هر N روز / هفته / ماه.

- Expected: The weekday grid hides, the N input appears, and its label matches the chosen mode.

**6.9** Choose سهمیه‌ای and set هفتگی/ماهانه plus a target count.

- Expected: Both controls apply to the review step and to the created challenge.

**6.10** Tick هدف جمعی and press مرحلهٔ بعد with amount or unit missing.

- Expected: Alert `برای هدف جمعی، هم مقدار و هم واحد رو وارد کن.`

**6.11** Reach step 3 and read the review card.

- Expected: Title, description, category, `— روز` duration, cadence label and the visibility note all match what you entered; the note text changes per عمومی / نامرئی / خصوصی.

**6.12** Publish with مدت چالش left empty.

- Expected: Challenge is created with no deadline — the detail page shows `—` under مهلت.

**6.13** Publish with مدت چالش = 30.

- Expected: The detail page's مهلت is 30 days from today (in the Persian calendar).

**6.14** Publish a valid challenge.

- Expected: 201 response, redirect to `/views/challenges/<new id>`, and you are auto-enrolled: CTA reads انصراف از چالش and شرکت‌کننده is 1.

**6.15** Publish after the session expired (delete the cookie first).

- Expected: Redirect to `/views/auth/?next=/views/challenges/create` — no raw 401 and no silent failure.

**6.16** Force a server-side validation error (e.g. a 51-character title).

- Expected: Alert shows `خطا در ثبت چالش: اطلاعات واردشده معتبر نیست.` — never a raw Pydantic error list.

**6.17** Press the X close button with a title/description filled in, then with an empty form.

- Expected: Filled: a confirm dialog first, and cancelling keeps you on the wizard. Empty: leaves straight to `/views/challenges/`.


### 7. Challenge detail — `/views/challenges/{id}`
**7.1** As user B, open a `private` challenge owned by user A.

- Expected: 404 page — not the challenge. An `unlisted` one opened by direct link does render.

**7.2** Open your own `private` and `unlisted` challenges.

- Expected: They render with a خصوصی or فقط با لینک tag next to the category.

**7.3** Open a challenge with a collective goal, then one without.

- Expected: With a goal the line reads `X از Y <unit> (٪)`; without it reads `N ثبت تکمیل‌شده (٪)`. The bar never exceeds 100% or goes below 0.

**7.4** Open a challenge you are not enrolled in.

- Expected: CTA reads ثبت‌نام در چالش, پیاپی من shows `—`, and there is no تاریخچه من section.

**7.5** Press the CTA and accept the confirm.

- Expected: شرکت‌کننده increments by one and the CTA flips to انصراف از چالش without a page reload.

**7.6** Enroll again from a second tab so the request returns 409.

- Expected: Treated as success — the CTA still ends up in the enrolled state, no error alert.

**7.7** Press the CTA (انصراف) and accept.

- Expected: شرکت‌کننده decrements and the CTA flips back to ثبت‌نام در چالش.

**7.8** Double-click the CTA quickly.

- Expected: Only one request is sent (the busy guard); the count changes by exactly one.

**7.9** Press the CTA after deleting the session cookie.

- Expected: Redirect to `/views/auth/?next=/views/challenges/<id>`.

**7.10** Open a `recurring_days` challenge you are enrolled in.

- Expected: تاریخچه من shows one row per real occurrence, newest first — never one row per calendar day — at most 12 rows, plus `فقط ۱۲ نوبت اخیر نشان داده شده.` when there are more.

**7.11** Read the history summary strip.

- Expected: Percentage, `completed از total نوبت`, longest streak with a flame, and این هفته done/total.

**7.12** Click a row that is already تکمیل‌شده or رد شده.

- Expected: Toast with that row's date and state; no sheet opens, nothing is re-recorded.

**7.13** Click ثبت on a pending row inside the 2-day backfill window and confirm.

- Expected: The row restyles to تکمیل‌شده (or رد شده), its ثبت button disappears, and a toast confirms — without a reload.

**7.14** Find an occurrence older than the backfill window.

- Expected: It has no ثبت button; forcing the POST returns 403 `Backfill window has closed`.

**7.15** Open a `recurring_quota` challenge you are enrolled in.

- Expected: History is a list of week/month rows (current plus up to 5 past), each with `done از target` and that many pips; only the current period has the ثبت نوبت جدید button.

**7.16** Record a quota check-in.

- Expected: The count and one more pip update in place, the `aria-label` follows, and the button disappears once the target is reached.

**7.17** Click the share button.

- Expected: Native share sheet where supported; otherwise the URL is copied and toast `لینک چالش کپی شد` appears. Cancelling the native sheet shows no error toast.

**7.18** Click the back arrow.

- Expected: Returns to `/views/challenges/`.

**7.19** At a 375px-wide viewport, scroll to the bottom.

- Expected: The sticky CTA sits above the bottom nav — neither covers the other or the last history row.


### 8. Profile — `/views/users/{id}`
**8.1** Open your own profile.

- Expected: Edit icon in the top bar, stats, and the full menu list including خروج از حساب.

**8.2** Open another user's profile.

- Expected: Header and stats only — no edit icon, no menu, no logout; their email is never shown.

**8.3** Check the stat cards against your home page.

- Expected: چالش فعال and تکمیل‌شده match the same counts shown on `/views/home/`.

**8.4** Click اطلاعات حساب, تنظیمات اعلان‌ها and دربارهٔ چالش.

- Expected: Each shows its own coming-soon toast; nothing navigates.

**8.5** Click تاریخچهٔ چالش‌ها.

- Expected: Navigates to `/views/home/`.

**8.6** Click خروج از حساب and cancel, then click it again and accept.

- Expected: Cancel keeps you logged in. Accept clears the cookie, lands on `/views/auth/`, and protected pages then redirect to login.

**8.7** Open `/views/users/999999`.

- Expected: 404 page, not a server error.


### 9. Dates & Farsi localization — `cross-page`
**9.1** Inspect any localized date element.

- Expected: The visible text is Persian-calendar (`data-jalali` still holds the original ISO value, unchanged).

**9.2** Read a quota history row label for a weekly and a monthly challenge.

- Expected: Weekly reads `هفتهٔ <day> <month>`; monthly reads `<month> <year>` in that order — never year-first.

**9.3** Inspect the `aria-label` of a timeline ثبت button.

- Expected: It is localized too, e.g. `ثبت پنجشنبه ۵ شهریور` — not the ISO date.

**9.4** Check any time-of-day display (schedule cards, deadline tooltips).

- Expected: 24-hour format, with no ق.ظ / ب.ظ.

**9.5** Set an enrollment's timezone to something far from your browser's (e.g. `America/New_York`) and open Today.

- Expected: The card's time and countdown are shown in the *enrollment's* timezone, not the browser's.

**9.6** Disable JavaScript and reload a page with dates.

- Expected: The ISO dates stay visible as plain text — no empty slots and no broken layout.

**9.7** Read عضو از on a profile.

- Expected: The join date is localized like every other date on the page.


### 10. Cross-cutting & resilience — `system`
**10.1** Enroll two accounts with different timezones in the same challenge, then compare their Today feeds around local midnight.

- Expected: Each sees the occurrence for their own local day; the same UTC instant does not force the same occurrence on both.

**10.2** Record the same occurrence twice (two tabs, or re-open the detail page and submit again).

- Expected: No duplicate row and no error card — the second attempt resolves to the existing check-in.

**10.3** After each mutating action (join, leave, check-in, create), reload the page.

- Expected: What the UI showed optimistically matches what the server renders — counters, states and streaks all agree.

**10.4** Watch the browser console throughout the run.

- Expected: No uncaught JS errors and no failed static asset requests on any page.

**10.5** Run the whole flow at a 375px-wide viewport.

- Expected: Category chips scroll horizontally, no page-level horizontal scrolling, and every control stays tappable.

**10.6** Tab through each page with the keyboard only.

- Expected: Every interactive control is reachable and has a visible focus state.

**10.7** Load a page that renders a challenge whose title/description contains `<script>` or other HTML.

- Expected: The markup is escaped and shown as text — no script executes and no layout breaks.


---

112 scenarios across 10 areas.
