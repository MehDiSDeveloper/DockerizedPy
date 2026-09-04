// چالش | comments.js — the conversation, as one modal per page
//
// One control opens it and one modal answers, wherever the control is: a
// card in the challenge list, a card in the rail's full-screen reader, the
// challenge's own page. That is the whole reason this is a modal and not the
// page it used to be — reading what people said about a challenge is not
// leaving the list you scrolled to find it, and a page took the reader's
// place with it.
//
// Three things keep it small:
//
//   * **The server is untouched.** The list is the same `/comments/fragment`
//     markup `createInfiniteScroller()` has always appended, asked for from
//     page one. Nothing here renders a comment.
//   * **One modal, re-aimed.** `subjectId` is a variable the scroller's
//     `buildUrl` closes over, so opening a second challenge's conversation is
//     a `reset()` rather than a second scroller, a second observer and a
//     second composer to keep in step.
//   * **The emoji catalogue is fetched, not shipped.** `emoji.js` is 14 KB
//     and this modal now rides on screens that are not about writing at all,
//     so it is loaded the first time somebody actually opens the keyboard.
//
// The opener is `[data-comments-id]` and it carries everything the modal
// needs to draw its header before a single request goes out: the id, the
// challenge's title, and the count already rendered on the control.

function initCommentModal() {
  const modal = document.getElementById("cmtModal");
  if (!modal) return;

  const titleEl = document.getElementById("cmtModalTitle");
  const totalEl = document.getElementById("cmtTotal");
  const listEl = document.getElementById("cmtList");
  const scrollEl = document.getElementById("cmtScroll");
  const emptyEl = document.getElementById("cmtEmpty");
  const signInEl = document.getElementById("cmtSignIn");

  let subjectId = null;
  // The control that opened it. Kept so a sent comment moves the number the
  // reader will see again the moment the modal goes back down.
  let opener = null;
  let scroller = null;
  let isOpen = false;
  // Whether *we* pushed the history entry the back gesture will pop.
  let pushed = false;

  // ---- opening and closing -------------------------------------------------

  document.addEventListener("click", (e) => {
    const btn = e.target.closest ? e.target.closest("[data-comments-id]") : null;
    if (!btn) return;
    // A card is a single `<a>`; without this the tap opens the challenge
    // instead of its conversation — the same guard the heart needs.
    e.preventDefault();
    e.stopPropagation();
    open(btn);
  });

  modal.addEventListener("click", (e) => {
    if (e.target.closest("[data-cm-close]")) close();
  });

  // Esc closes it, and so does the phone's back gesture: opening pushes a
  // history entry, so `popstate` is the same «go back» the rail's reader
  // already answers to.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && isOpen) close();
  });
  window.addEventListener("popstate", () => {
    if (isOpen) close(true);
  });

  function open(btn) {
    opener = btn;
    subjectId = btn.dataset.commentsId;
    titleEl.textContent = btn.dataset.commentsTitle || "نظرها";
    setTotal(Number(btn.dataset.commentsCount) || 0);

    modal.hidden = false;
    // The page behind must not scroll under a full-height overlay.
    document.body.classList.add("cm-modal-open");
    isOpen = true;
    scrollEl.scrollTop = 0;
    if (signInEl) {
      signInEl.href =
        "/views/auth/?next=" +
        encodeURIComponent(window.location.pathname + window.location.search);
    }

    if (!scroller) scroller = buildScroller();
    scroller.reset();

    if (!pushed) {
      history.pushState({ cmtModal: true }, "");
      pushed = true;
    }
  }

  function close(fromPop) {
    if (!isOpen) return;
    isOpen = false;
    modal.hidden = true;
    document.body.classList.remove("cm-modal-open");
    if (composer) resetComposer();
    // A close that did not come *from* the back gesture has to consume the
    // entry the open pushed, or the reader's next back press does nothing.
    if (pushed && !fromPop) history.back();
    pushed = false;
  }

  function setTotal(n) {
    totalEl.dataset.total = String(n);
    totalEl.textContent = window.faDigits(n) + " نظر";
  }

  function buildScroller() {
    return createInfiniteScroller({
      container: listEl,
      sentinel: document.getElementById("cmtSentinel"),
      loadingEl: document.getElementById("cmtLoading"),
      emptyEl,
      pageSize: 10,
      initialOffset: 0,
      initialHasMore: true,
      buildUrl: (offset, limit) =>
        `/views/challenges/${subjectId}/comments/fragment?offset=${offset}&limit=${limit}`,
      onEmpty: () => {
        emptyEl.hidden = false;
      },
    });
  }

  // ---- the list's three actions, one delegated listener --------------------
  // Threads arrive by infinite scroll, so every handler reads its id off the
  // element that was tapped rather than off a list built when the modal
  // opened.
  listEl.addEventListener("click", (e) => {
    const more = e.target.closest("[data-show-replies]");
    if (more) {
      more
        .closest(".cm-replies")
        .querySelectorAll(".cm-reply-slot[hidden]")
        .forEach((slot) => {
          slot.hidden = false;
        });
      more.remove();
      return;
    }

    const replyBtn = e.target.closest("[data-reply-to]");
    if (replyBtn) {
      startReply(replyBtn.dataset.replyTo, replyBtn.dataset.replyName);
      return;
    }

    const delBtn = e.target.closest("[data-delete-comment]");
    if (delBtn) confirmDelete(delBtn);
  });

  function confirmDelete(btn) {
    // A sheet rather than confirm(), like every other irreversible action in
    // this app: only a sheet can name what else goes with it.
    createSheet({
      title: "حذف نظر",
      fields: [
        {
          type: "note",
          text:
            btn.dataset.isRoot === "true"
              ? "این نظر و همهٔ پاسخ‌هایش حذف می‌شوند. این کار برگشت‌پذیر نیست."
              : "این پاسخ حذف می‌شود. این کار برگشت‌پذیر نیست.",
        },
      ],
      confirmLabel: "لغو",
      onConfirm: () => true,
      danger: {
        label: "حذف",
        onClick: async () => {
          const { ok } = await apiFetch(`/comments/${btn.dataset.deleteComment}`, {
            method: "DELETE",
          });
          if (ok) scroller.reset();
          else showToast("حذف انجام نشد. دوباره تلاش کن.");
        },
      },
    });
  }

  // ---- the composer --------------------------------------------------------

  const composer = document.getElementById("cmtComposer");
  if (!composer) return;

  const bodyEl = document.getElementById("cmtBody");
  const sendEl = document.getElementById("cmtSend");
  const emojiBtn = document.getElementById("cmtEmojiBtn");
  const replyingEl = document.getElementById("cmtReplying");
  const replyNameEl = document.getElementById("cmtReplyName");

  let parentId = null;
  // Where the next emoji goes. Remembered rather than read at insert time:
  // opening the keyboard blurs the textarea (so a phone's own keyboard gets
  // out of the way), and an unfocused textarea reports its caret at the
  // *end* — which is exactly how every emoji ends up after the text instead
  // of where the writer was.
  let caret = 0;

  function rememberCaret() {
    caret = bodyEl.selectionStart ?? bodyEl.value.length;
  }
  ["input", "click", "keyup", "select", "blur", "focus"].forEach((ev) => {
    bodyEl.addEventListener(ev, rememberCaret);
  });

  function sync() {
    sendEl.disabled = !bodyEl.value.trim();
    // The textarea grows with its content instead of scrolling inside a
    // one-line box. Back to one line whenever it is empty: measuring an empty
    // textarea's scrollHeight before the modal has settled hands back a
    // full-height box, which is how the composer ends up opening at its
    // maximum.
    if (!bodyEl.value) {
      bodyEl.style.height = "";
      return;
    }
    bodyEl.style.height = "auto";
    // +2 for the border box: an exact scrollHeight leaves the textarea one
    // pixel short of its own content and shows a scrollbar over one line.
    bodyEl.style.height = Math.min(bodyEl.scrollHeight + 2, 140) + "px";
    bodyEl.style.overflowY = bodyEl.scrollHeight > 140 ? "auto" : "hidden";
  }

  function startReply(id, name) {
    parentId = id;
    replyNameEl.textContent = name;
    replyingEl.hidden = false;
    bodyEl.focus();
  }

  function cancelReply() {
    parentId = null;
    replyingEl.hidden = true;
  }

  // Whatever was half-typed must not follow the modal to the next challenge:
  // a reply aimed at somebody else's thread would be sent into a conversation
  // they are not in.
  function resetComposer() {
    bodyEl.value = "";
    caret = 0;
    cancelReply();
    closePicker();
    sync();
  }

  document.getElementById("cmtReplyCancel").addEventListener("click", cancelReply);

  // ---- the emoji keyboard --------------------------------------------------
  // It writes into the text at the caret rather than sending anything of its
  // own: an emoji is part of what somebody wrote.
  let picker = null;
  let catalogue = null;

  // The catalogue is a generated file (`emoji.js`), fetched once and only if
  // the keyboard is actually opened — this modal is included on screens whose
  // readers mostly never write.
  function loadCatalogue() {
    if (window.EMOJI_GROUPS) return Promise.resolve();
    if (!catalogue) {
      catalogue = new Promise((resolve, reject) => {
        const el = document.createElement("script");
        el.src = "/static/js/emoji.js";
        el.onload = resolve;
        el.onerror = reject;
        document.head.appendChild(el);
      });
    }
    return catalogue;
  }

  emojiBtn.addEventListener("click", async () => {
    if (!picker) {
      try {
        await loadCatalogue();
      } catch (err) {
        showToast("صفحه‌کلید اموجی بارگذاری نشد. دوباره تلاش کن.");
        return;
      }
      // Built after the catalogue lands: createEmojiPicker reads
      // `window.EMOJI_GROUPS` once, when it draws its tabs.
      picker = createEmojiPicker({
        panel: document.getElementById("cmtEmoji"),
        tabsEl: document.getElementById("cmtEmojiTabs"),
        labelEl: document.getElementById("cmtEmojiLabel"),
        gridEl: document.getElementById("cmtEmojiGrid"),
        emptyEl: document.getElementById("cmtEmojiEmpty"),
        onPick: (char) => {
          const value = bodyEl.value;
          const at = Math.min(caret, value.length);
          bodyEl.value = value.slice(0, at) + char + value.slice(at);
          caret = at + char.length;
          // Set even while unfocused, so the caret is already in the right
          // place when the writer taps back into the box.
          try {
            bodyEl.setSelectionRange(caret, caret);
          } catch (err) {
            /* not focusable yet */
          }
          sync();
        },
      });
    }
    picker.toggle();
    emojiBtn.classList.toggle("is-open", picker.isOpen());
    emojiBtn.setAttribute("aria-expanded", picker.isOpen() ? "true" : "false");
    // The panel takes the keyboard's place rather than fighting it for the
    // bottom of a phone screen.
    if (picker.isOpen()) bodyEl.blur();
    else bodyEl.focus();
  });

  function closePicker() {
    if (!picker || !picker.isOpen()) return;
    picker.close();
    emojiBtn.classList.remove("is-open");
    emojiBtn.setAttribute("aria-expanded", "false");
  }

  // Esc puts the keyboard away first, like every other layer in this app.
  // Capture, and swallowed, so an open keyboard is what Esc closes rather
  // than the whole modal out from under it.
  document.addEventListener(
    "keydown",
    (e) => {
      if (e.key === "Escape" && picker && picker.isOpen()) {
        e.stopPropagation();
        closePicker();
      }
    },
    true
  );

  bodyEl.addEventListener("input", sync);

  sendEl.addEventListener("click", async () => {
    if (sendEl.disabled) return;
    sendEl.disabled = true;
    const { ok } = await apiFetch(`/comments/challenge/${subjectId}`, {
      method: "POST",
      body: {
        body: bodyEl.value.trim() || null,
        parent_id: parentId ? Number(parentId) : null,
      },
    });
    if (!ok) {
      showToast("ارسال نشد. دوباره تلاش کن.");
      sendEl.disabled = false;
      return;
    }
    resetComposer();
    // The list is reloaded rather than spliced by hand: threads are
    // newest-first and a reply lands inside its own thread, so *where* a
    // comment belongs is a server-side answer — the same trade every check-in
    // on challenge-detail makes.
    await scroller.reset();
    emptyEl.hidden = true;
    scrollEl.scrollTop = 0;
    bumpCount();
  });

  // The two places the number lives: the modal's header, and the control the
  // reader came in through — which is still on the page behind and would
  // otherwise contradict what they just did.
  function bumpCount() {
    const n = Number(totalEl.dataset.total) + 1;
    setTotal(n);
    if (!opener) return;
    opener.dataset.commentsCount = String(n);
    const el = opener.querySelector("[data-comment-count]");
    if (el) el.textContent = window.faDigits(n);
  }

  sync();
}

document.addEventListener("DOMContentLoaded", () => initCommentModal());
