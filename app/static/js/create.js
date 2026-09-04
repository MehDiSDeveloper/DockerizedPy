/* «چیز تازه» -- one create entrance for all three objects.
 *
 * A challenge, a group and a roadmap are three instances of one idea to a
 * member: something you make, manage, and that has people in it. Three
 * different «+» buttons doing three different things meant the answer to
 * "how do I make one" depended on which screen you happened to be standing
 * on. So every `.fab` in the app now carries `data-create-fab` and opens the
 * same picker.
 *
 * **Depth matched to the object, shell shared.** A group and a course ask
 * three short questions and are built in a `createSheet` right here; a
 * challenge has a cadence, a schedule and a category and keeps its
 * full-screen wizard -- that complexity is real and flattening it would only
 * move it. What is the same in all three is the way in, the header, where the
 * primary button sits, and the way out.
 *
 * Attributes on the FAB:
 *   data-create-fab            -- opt in
 *   data-create-kinds="a,b,c"  -- which kinds this context offers
 *                                 (default: challenge,group,roadmap)
 *   data-create-group="<id>"   -- create the challenge under this group
 *
 * The emblem catalogue is read from an optional `#emblemOptions` JSON block
 * -- the same one the group screens already ship. Where a page has none, the
 * group sheet simply omits the picture question and the group's own manage
 * screen answers it later.
 */
(function () {
  const KIND_OPTIONS = [
    { value: "company", label: "شرکت", icon: "building" },
    { value: "school", label: "مدرسه", icon: "school" },
    { value: "team", label: "تیم", icon: "users" },
    { value: "other", label: "سایر", icon: "catOther" },
  ];

  function emblemOptions() {
    const el = document.getElementById("emblemOptions");
    if (!el) return null;
    try {
      return JSON.parse(el.textContent);
    } catch (e) {
      return null;
    }
  }

  function createGroup() {
    const emblems = emblemOptions();
    createSheet({
      title: "گروه جدید",
      confirmLabel: "بساز",
      fields: [
        { name: "name", label: "نام گروه", required: true, maxlength: 60,
          placeholder: "مثلاً: شرکت آبی" },
        { name: "kind", label: "این گروه چیست؟", type: "chips", value: "company",
          options: KIND_OPTIONS },
        { name: "description", label: "توضیح", type: "textarea", rows: 2, maxlength: 500,
          placeholder: "اختیاری", hint: "اعضا این را روی صفحهٔ گروه می‌بینند." },
        ...(emblems
          ? [{ name: "emblem", label: "آیکون گروه", type: "avatars", value: "",
              options: emblems.map((a, i) => ({
                value: a.id, url: a.url, label: "نشان " + (i + 1),
              })) }]
          : []),
      ],
      onConfirm: async (values) => {
        const { ok, data } = await apiFetch("/groups/", {
          method: "POST",
          body: {
            name: values.name.trim(),
            kind: values.kind || "other",
            description: values.description.trim() || null,
            emblem: values.emblem || null,
          },
        });
        if (!ok) {
          showToast("ساخت گروه انجام نشد. دوباره تلاش کن.");
          return false;
        }
        window.location.href = `/views/groups/${data.id}`;
        return true;
      },
    });
  }

  function createRoadmap() {
    createSheet({
      title: "مسیر جدید",
      confirmLabel: "بساز",
      fields: [
        { name: "title", label: "نام مسیر", required: true, maxlength: 60,
          placeholder: "مثلاً: سی روز آرام‌تر" },
        { name: "description", label: "توضیح", type: "textarea", rows: 3, maxlength: 1000,
          placeholder: "اختیاری",
          hint: "کسی که مسیر را باز می‌کند اول همین را می‌خواند." },
        { name: "visibility", label: "چه کسانی ببینند؟", type: "chips", value: "public",
          hint: "«با لینک» یعنی در فهرست عمومی نمی‌آید و فقط با لینک تو باز می‌شود.",
          options: [
            { value: "public", label: "همه", icon: "eye" },
            { value: "unlisted", label: "با لینک", icon: "link" },
            { value: "private", label: "فقط خودم", icon: "lock" },
          ] },
        { name: "strict", label: "ترتیب قدم‌ها", type: "chips", value: "soft",
          hint: "«پیشنهادی» یعنی قدم‌های بعدی هم باز است و فقط نشان می‌دهد وعده‌اش نرسیده.",
          options: [
            { value: "soft", label: "پیشنهادی", icon: "route" },
            { value: "strict", label: "اجباری", icon: "lock" },
          ] },
      ],
      onConfirm: async (values) => {
        const { ok, data } = await apiFetch("/roadmaps/", {
          method: "POST",
          body: {
            title: values.title.trim(),
            description: values.description.trim() || null,
            visibility: values.visibility || "public",
            strict: values.strict === "strict",
          },
        });
        if (!ok) {
          showToast("ساخت مسیر انجام نشد. دوباره تلاش کن.");
          return false;
        }
        // Straight to the management screen: a course with no steps is not a
        // course yet, and the next thing to do is add the first step.
        window.location.href = `/views/roadmaps/${data.id}/manage`;
        return true;
      },
    });
  }

  function openWizard(groupId) {
    const p = new URLSearchParams();
    if (groupId) p.set("group", groupId);
    // Where the wizard goes when it is closed: the page the «+» was tapped
    // on, so leaving a half-filled form puts the member back where they were
    // rather than on a list they did not come from.
    p.set("from", window.location.pathname + window.location.search);
    window.location.href = `/views/challenges/create?${p.toString()}`;
  }

  const BUILDERS = {
    challenge: {
      label: "چالش",
      icon: "target",
      hint: "کاری که خودت یا دیگران هر روز یا هر هفته انجام می‌دهید.",
      open: (fab) => openWizard(fab.dataset.createGroup),
    },
    group: {
      label: "گروه",
      icon: "building",
      hint: "فضای یک شرکت، مدرسه یا تیم برای چالش‌های مشترک.",
      open: createGroup,
    },
    roadmap: {
      label: "مسیر",
      icon: "route",
      hint: "چند چالش پشت سر هم، که قدم‌به‌قدم باز می‌شوند.",
      open: createRoadmap,
    },
  };

  function openPicker(fab) {
    const kinds = (fab.dataset.createKinds || "challenge,group,roadmap")
      .split(",")
      .map((k) => k.trim())
      .filter((k) => BUILDERS[k]);

    // One offer is not a question: a context that can only build a challenge
    // opens the wizard instead of asking which of one thing they meant.
    if (kinds.length === 1) {
      BUILDERS[kinds[0]].open(fab);
      return;
    }

    createSheet({
      title: "چه چیزی می‌سازی؟",
      fields: [
        { name: "kind", label: "نوع", type: "chips", value: kinds[0],
          options: kinds.map((k) => ({
            value: k, label: BUILDERS[k].label, icon: BUILDERS[k].icon,
          })) },
        // One line per kind rather than a hint on the field: the difference
        // between the three is the whole question being asked.
        ...kinds.map((k) => ({
          type: "note",
          text: `${BUILDERS[k].label}: ${BUILDERS[k].hint}`,
        })),
      ],
      confirmLabel: "ادامه",
      onConfirm: (values) => {
        const build = BUILDERS[values.kind];
        if (!build) return false;
        // After the picker's own sheet is gone -- nothing in this app ever
        // stacks two backdrops.
        setTimeout(() => build.open(fab), 0);
        return true;
      },
    });
  }

  function initCreateFab() {
    document.querySelectorAll("[data-create-fab]").forEach((fab) => {
      fab.addEventListener("click", (e) => {
        e.preventDefault();
        openPicker(fab);
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initCreateFab);
  } else {
    initCreateFab();
  }

  window.initCreateFab = initCreateFab;
})();
