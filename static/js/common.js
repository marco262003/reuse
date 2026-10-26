/* Shared helpers: formatting, modals, toasts, form errors, header (bell, name, clock). */
(function () {
  const R = (window.REUSE = window.REUSE || {});

  // ---------- Formatting ----------
  const esc = (value) =>
    String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pad = (n) => String(n).padStart(2, "0");

  const fmt = {
    date(iso) {
      if (!iso) return "–";
      const d = new Date(iso);
      return `${pad(d.getMonth() + 1)}/${pad(d.getDate())}/${d.getFullYear()}`;
    },
    time(iso) {
      if (!iso) return "–";
      return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
    },
    dateTime(iso) {
      return iso ? `${fmt.date(iso)} | ${fmt.time(iso)}` : "–";
    },
    long(iso) {
      if (!iso) return "–";
      const d = new Date(iso);
      const day = d.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
      return `${day} | ${fmt.time(iso)}`;
    },
    clock(d = new Date()) {
      const h = d.getHours() % 12 || 12;
      return `${fmt.date(d)} | ${pad(h)}:${pad(d.getMinutes())} ${d.getHours() < 12 ? "AM" : "PM"}`;
    },
    ago(iso) {
      const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso)) / 1000));
      if (seconds < 60) return "Just now";
      const minutes = Math.floor(seconds / 60);
      if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
      const hours = Math.floor(minutes / 60);
      if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
      const days = Math.floor(hours / 24);
      if (days < 30) return `${days} day${days === 1 ? "" : "s"} ago`;
      return fmt.date(iso);
    },
    dueText(iso) {
      const ms = new Date(iso) - Date.now();
      const day = 86400000;
      if (ms < 0) {
        const d = Math.floor(-ms / day);
        return d >= 1 ? `Overdue by ${d} day${d > 1 ? "s" : ""}` : "Overdue";
      }
      const d = Math.floor(ms / day);
      if (d >= 1) return `${d} day${d > 1 ? "s" : ""} left`;
      const h = Math.floor(ms / 3600000);
      return h >= 1 ? `${h} hour${h > 1 ? "s" : ""} left` : "Less than 1 hour left";
    },
    toDateInput(iso) {
      const d = new Date(iso);
      return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
    },
    toTimeInput(iso) {
      const d = new Date(iso);
      return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
    },
  };

  R.esc = esc;
  R.fmt = fmt;
  R.badge = (status, label) => `<span class="badge badge-${esc(status)}">${esc(label)}</span>`;
  R.debounce = (fn, ms) => {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  };

  // Broken or missing photos fall back to the placeholder picture.
  document.addEventListener("error", (e) => {
    const el = e.target;
    if (el.tagName === "IMG" && R.placeholder && !el.dataset.fallback) {
      el.dataset.fallback = "1";
      el.src = R.placeholder;
    }
  }, true);

  // ---------- Modals ----------
  const stack = [];
  const CLOSE_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/></svg>';

  R.modal = {
    open(html, opts = {}) {
      const overlay = document.createElement("div");
      overlay.className = "modal-overlay";
      overlay.innerHTML =
        `<div class="modal ${esc(opts.className || "")}" role="dialog" aria-modal="true" aria-label="${esc(opts.label || "Dialog")}">` +
        `<button type="button" class="modal-close" aria-label="Close">${CLOSE_ICON}</button>` +
        `<div class="modal-body">${html}</div></div>`;
      const previous = document.activeElement;
      const modal = {
        el: overlay,
        body: overlay.querySelector(".modal-body"),
        close() {
          const i = stack.indexOf(modal);
          if (i === -1) return;
          stack.splice(i, 1);
          overlay.remove();
          if (!stack.length) document.body.classList.remove("modal-open");
          if (previous && previous.focus && document.contains(previous)) previous.focus();
          if (opts.onClose) opts.onClose();
        },
      };
      overlay.querySelector(".modal-close").addEventListener("click", modal.close);
      overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) modal.close(); });
      document.getElementById("modal-root").appendChild(overlay);
      document.body.classList.add("modal-open");
      stack.push(modal);
      const first = overlay.querySelector("input:not([type=hidden]), select, textarea, .modal-body button, .modal-body a");
      (first || overlay.querySelector(".modal-close")).focus();
      R.bindEyes(overlay);
      return modal;
    },
    closeAll() { [...stack].forEach((m) => m.close()); },
  };

  document.addEventListener("keydown", (e) => {
    const top = stack[stack.length - 1];
    if (!top) return;
    if (e.key === "Escape") { top.close(); return; }
    if (e.key === "Tab") { // keep keyboard focus inside the open dialog
      const items = [...top.el.querySelectorAll("button:not([disabled]), input, select, textarea, a[href]")].filter((n) => n.offsetParent !== null);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  });

  R.confirm = ({ title, message, confirmText = "Confirm", cancelText = "Cancel" }) =>
    new Promise((resolve) => {
      const modal = R.modal.open(
        `<h2 class="modal-title">${esc(title)}</h2><p class="modal-text">${esc(message)}</p>` +
        `<div class="modal-actions"><button type="button" class="btn btn-outline" data-no>${esc(cancelText)}</button>` +
        `<button type="button" class="btn btn-primary" data-yes>${esc(confirmText)}</button></div>`,
        { className: "modal-sm", label: title, onClose: () => resolve(false) }
      );
      modal.body.querySelector("[data-no]").addEventListener("click", () => modal.close());
      modal.body.querySelector("[data-yes]").addEventListener("click", () => { resolve(true); modal.close(); });
    });

  R.toast = (message, type = "info") => {
    const root = document.getElementById("toast-root");
    if (!root) return;
    const el = document.createElement("div");
    el.className = `toast is-${type}`;
    el.setAttribute("role", type === "error" ? "alert" : "status");
    el.textContent = message;
    root.appendChild(el);
    setTimeout(() => el.remove(), type === "error" ? 6000 : 3500);
  };

  // ---------- Forms ----------
  R.eyeButton = () =>
    '<button type="button" class="eye-btn" aria-label="Show password" aria-pressed="false">' +
    '<svg class="icon-on" viewBox="0 0 24 24" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>' +
    '<svg class="icon-off" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 5.1A10.7 10.7 0 0 1 12 5c6.5 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.2M6.6 6.6A17.3 17.3 0 0 0 2 12s3.5 7 10 7a10.8 10.8 0 0 0 4.4-.9"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg></button>';

  R.bindEyes = (root = document) => {
    root.querySelectorAll(".eye-btn").forEach((btn) => {
      if (btn.dataset.bound) return;
      btn.dataset.bound = "1";
      btn.addEventListener("click", () => {
        const input = btn.parentElement.querySelector("input");
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        btn.setAttribute("aria-pressed", String(show));
        btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      });
    });
  };

  R.clearErrors = (root) => {
    root.querySelectorAll("[data-field]").forEach((field) => {
      const err = field.querySelector(".field-error");
      if (err) err.textContent = "";
      field.querySelectorAll("[name]").forEach((i) => i.removeAttribute("aria-invalid"));
    });
  };

  R.showErrors = (root, errors) => {
    Object.entries(errors || {}).forEach(([key, message]) => {
      const field = root.querySelector(`[data-field="${key}"]`);
      if (!field) return;
      const err = field.querySelector(".field-error");
      if (err) err.textContent = message;
      const input = field.querySelector("[name]");
      if (input) input.setAttribute("aria-invalid", "true");
    });
    const firstBad = root.querySelector('[aria-invalid="true"]');
    if (firstBad) firstBad.focus();
  };

  R.alert = (el, message, type = "error") => {
    if (!el) return;
    el.textContent = message || "";
    el.className = `form-alert is-${type}`;
    el.hidden = !message;
  };

  // ---------- Loading / empty / error states ----------
  R.state = {
    loading(el, text = "Loading…") {
      el.innerHTML = `<div class="state" role="status"><span class="spinner" aria-hidden="true"></span><p>${esc(text)}</p></div>`;
    },
    empty(el, text, linkHtml = "") {
      el.innerHTML = `<div class="state"><p>${esc(text)}</p>${linkHtml}</div>`;
    },
    error(el, text, retry) {
      el.innerHTML = `<div class="state" role="alert"><p>${esc(text)}</p><button type="button" class="btn btn-outline btn-sm" data-retry>Try again</button></div>`;
      el.querySelector("[data-retry]").addEventListener("click", retry);
    },
  };

  // ---------- Live refresh ----------
  R.changed = () => document.dispatchEvent(new CustomEvent("reuse:changed"));
  R.onChanged = (fn) => document.addEventListener("reuse:changed", fn);
  R.poll = (fn, ms) =>
    setInterval(() => {
      if (document.hidden || document.querySelector(".modal-overlay")) return;
      fn();
    }, ms);

  // ---------- Signed-in header ----------
  function setBadge(count) {
    const badge = document.getElementById("bell-badge");
    if (!badge) return;
    badge.hidden = !count;
    badge.textContent = count > 99 ? "99+" : String(count || "");
    const bell = badge.closest("a");
    if (bell) bell.setAttribute("aria-label", count ? `Notifications, ${count} unread` : "Notifications");
  }

  R.refreshBadge = async () => {
    const res = await R.api.get("/api/me/");
    if (res.ok) {
      R.user = res.user;
      setBadge(res.unread_count);
      const name = document.getElementById("user-name");
      if (name) name.textContent = res.user.short_name;
    }
    return res;
  };
  R.setBadge = setBadge;

  R.userReady = (async () => {
    if (!document.body.classList.contains("app")) return null;
    const clock = document.querySelector("[data-clock]");
    if (clock) {
      const tick = () => { clock.textContent = fmt.clock(); };
      tick();
      setInterval(tick, 10000);
    }
    const res = await R.refreshBadge();
    setInterval(R.refreshBadge, 60000);
    return res.ok ? res.user : null;
  })();
})();
