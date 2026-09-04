/* «انتخاب عکس» — one control for every picture this app stores.
 *
 * Three steps behind one promise: the phone's own file chooser, a crop locked
 * to the frame the surface will actually draw, and the upload. `pickImage()`
 * resolves to `{key, url}` — a media key from `POST /media/`, which the caller
 * then writes onto whatever column wanted it (`PATCH /users/{id}`,
 * `PATCH /challenges/{id}`, or the create wizard's payload). It never touches
 * a row itself, which is what lets the create wizard — where no row exists
 * yet — use exactly the same picker as an edit sheet.
 *
 * Why a crop step at all: every surface here draws its picture with
 * `object-fit:cover`, so an uncropped photo is *silently* cut by the browser
 * and the member finds out later that their face is outside the frame. The
 * cropper is that cut, made visible and theirs. It is locked to the frame's
 * own aspect rather than free, because a shape the layout cannot honour is a
 * promise the app then breaks.
 *
 * Cropper.js is vendored under `static/vendor/`, not pulled from a CDN — the
 * same call the avatars and the emoji catalogue make, for the same reason:
 * the app has to work inside the Docker image with no outbound network. It is
 * loaded on the *first pick* rather than on page load, because most readers
 * of a page carrying a picker never open it (the emoji catalogue's trade).
 *
 * The canvas re-encodes to WebP before the request goes out, so what crosses
 * the network is ~100KB rather than a 6MB phone photograph — on a mobile
 * connection that is the difference between a picker that feels instant and
 * one people give up on. The server re-encodes again regardless: this is a
 * courtesy to the connection, never a substitute for validation.
 */

// The frames the app has surfaces for. `shape` is the server's own name for
// it (`app/media.py::SHAPES`), so the two halves cannot drift apart; `w`/`h`
// are the canvas the crop is rendered at, which is also the server's ceiling.
const IMAGE_FRAMES = {
  avatar: { shape: "avatar", w: 512, h: 512, title: "تصویر پروفایل" },
  challenge_square: { shape: "challenge_square", w: 1080, h: 1080, title: "تصویر کارت چالش" },
  challenge_tall: { shape: "challenge_tall", w: 1080, h: 1920, title: "تصویر سربرگ چالش" },
  roadmap_square: { shape: "roadmap_square", w: 1080, h: 1080, title: "تصویر مسیر" },
};

const CROPPER_JS = "/static/vendor/cropper/cropper.min.js";
const CROPPER_CSS = "/static/vendor/cropper/cropper.min.css";

let cropperReady = null;

function loadCropper() {
  // One promise for the whole page: two pickers opened in a row must not
  // append the script twice.
  if (cropperReady) return cropperReady;
  cropperReady = new Promise((resolve, reject) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = CROPPER_CSS;
    document.head.appendChild(link);
    const script = document.createElement("script");
    script.src = CROPPER_JS;
    script.onload = resolve;
    script.onerror = () => reject(new Error("cropper"));
    document.head.appendChild(script);
  });
  return cropperReady;
}

/** Ask the OS for one image file. Resolves to a File, or null if dismissed.
 *
 * The input is thrown away after each use rather than kept around: a reused
 * <input type="file"> fires no `change` when the member picks the *same*
 * file again, which reads as a dead button. `capture` is deliberately not
 * set — that would force the camera and hide the camera roll, which is where
 * the picture people actually want almost always is. */
function chooseFile() {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.style.display = "none";
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      input.remove();
      resolve(value);
    };
    input.addEventListener("change", () => finish(input.files && input.files[0] ? input.files[0] : null));
    // A dismissed chooser fires nothing on most browsers; `cancel` is the
    // modern signal, and the focus fallback covers the rest so a caller
    // awaiting this is never left hanging.
    input.addEventListener("cancel", () => finish(null));
    window.addEventListener("focus", () => setTimeout(() => finish(input.files && input.files[0] ? input.files[0] : null), 400), { once: true });
    document.body.appendChild(input);
    input.click();
  });
}

function canvasToBlob(canvas) {
  return new Promise((resolve) => {
    // Quality is applied to WebP; a browser that does not encode WebP falls
    // back to PNG here and the server re-encodes it either way.
    canvas.toBlob((blob) => resolve(blob), "image/webp", 0.9);
  });
}

/** The crop step. Resolves to a Blob, or null if the member backed out. */
function cropTo(file, frame) {
  return new Promise((resolve) => {
    const objectUrl = URL.createObjectURL(file);

    const modal = document.createElement("div");
    modal.className = "ip-modal";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-label", frame.title);
    modal.innerHTML = `
      <div class="sheet-backdrop"></div>
      <div class="ip-panel glass">
        <div class="sheet-head">
          <h3>${frame.title}</h3>
          <button type="button" class="icon-btn" data-icon="x" aria-label="بستن" data-ip-close></button>
        </div>
        <p class="ip-hint">با کشیدن و دو انگشت، بخشی از عکس را که می‌خواهی دیده شود در قاب بگذار.</p>
        <div class="ip-stage"><img alt=""></div>
        <div class="sheet-actions">
          <button type="button" class="btn btn-ghost" data-ip-close>لغو</button>
          <button type="button" class="btn btn-ghost btn-accent" data-ip-confirm>انتخاب</button>
        </div>
      </div>`;

    const image = modal.querySelector(".ip-stage img");
    image.src = objectUrl;
    document.body.appendChild(modal);
    if (window.renderIcons) window.renderIcons(modal);
    document.body.style.overflow = "hidden";
    // see createSheet(): on a wide screen the scroller is the phone column
    document.body.classList.add("is-scroll-locked");

    const cropper = new window.Cropper(image, {
      aspectRatio: frame.w / frame.h,
      viewMode: 2,
      dragMode: "move",
      autoCropArea: 1,
      background: false,
      movable: true,
      zoomable: true,
      responsive: true,
      // The frame is fixed and the *picture* moves inside it — the gesture a
      // phone's own photo editor uses. A draggable box on a 360px screen is
      // eight handles nobody can hit.
      cropBoxMovable: false,
      cropBoxResizable: false,
      toggleDragModeOnDblclick: false,
    });

    let settled = false;
    function close(value) {
      if (settled) return;
      settled = true;
      cropper.destroy();
      URL.revokeObjectURL(objectUrl);
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = "";
      document.body.classList.remove("is-scroll-locked");
      modal.remove();
      resolve(value);
    }
    function onKey(e) {
      if (e.key === "Escape") {
        e.stopPropagation();
        close(null);
      }
    }
    document.addEventListener("keydown", onKey, true);

    modal.querySelectorAll("[data-ip-close]").forEach((el) => el.addEventListener("click", () => close(null)));
    modal.querySelector(".sheet-backdrop").addEventListener("click", () => close(null));
    modal.querySelector("[data-ip-confirm]").addEventListener("click", async () => {
      const canvas = cropper.getCroppedCanvas({
        width: frame.w,
        height: frame.h,
        imageSmoothingQuality: "high",
        fillColor: "#fff",
      });
      if (!canvas) return close(null);
      close(await canvasToBlob(canvas));
    });
  });
}

/** Choose → crop → upload. Resolves to `{key, url}` or null.
 *
 * `frameKey` is one of IMAGE_FRAMES. Every refusal along the way — a
 * dismissed chooser, a cancelled crop, a rejected file — resolves to null
 * rather than throwing, so a caller is one `if` and never a try/catch.
 */
async function pickImage(frameKey) {
  const frame = IMAGE_FRAMES[frameKey];
  if (!frame) throw new Error(`unknown image frame: ${frameKey}`);

  const file = await chooseFile();
  if (!file) return null;

  try {
    await loadCropper();
  } catch {
    window.showToast("ابزار برش عکس بارگذاری نشد. دوباره تلاش کن.");
    return null;
  }

  const blob = await cropTo(file, frame);
  if (!blob) return null;

  const body = new FormData();
  body.append("shape", frame.shape);
  body.append("file", blob, "upload.webp");
  // Not `apiFetch`: that one sets a JSON content type, and a multipart body
  // needs the browser to write its own boundary. The 401 redirect it would
  // have given is spelled out below instead.
  let res;
  try {
    res = await fetch("/media/", { method: "POST", body });
  } catch {
    window.showToast("آپلود عکس انجام نشد. دوباره تلاش کن.");
    return null;
  }
  if (res.status === 401) {
    window.location.href = `/views/auth/?next=${encodeURIComponent(window.location.pathname)}`;
    return null;
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    window.showToast(typeof err.detail === "string" ? err.detail : "آپلود عکس انجام نشد. دوباره تلاش کن.");
    return null;
  }
  return res.json();
}

window.pickImage = pickImage;
