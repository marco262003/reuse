/* Reset password and choose new password, plus helpers shared by the sign-in pages. */
(function () {
  const R = window.REUSE;
  const $ = (selector) => document.querySelector(selector);
  const alertBox = () => $("#form-alert");

  const REQUIRED = "Please fill out this field.";
  const PASSWORD_HELP = "Use at least 8 characters, with an uppercase letter, a number and a special character.";
  const EMAIL_RE = /^[A-Za-z0-9._%+\-]+@g\.batstate-u\.edu\.ph$/i;
  const NAME_RE = /^\p{L}+(?:[ '\-]\p{L}+)*$/u;

  const passwordOk = (p) => p.length >= 8 && /[A-Z]/.test(p) && /\d/.test(p) && /[^A-Za-z0-9]/.test(p);

  // 0917 123 4567 / 9171234567 / +63 917 123 4567  ->  9171234567 (or "" when invalid)
  function mobileDigits(value) {
    let d = String(value || "").replace(/[\s\-().]/g, "");
    if (d.startsWith("+63")) d = d.slice(3);
    else if (d.startsWith("63")) d = d.slice(2);
    else if (d.startsWith("0")) d = d.slice(1);
    return /^9\d{9}$/.test(d) ? d : "";
  }

  function setBusy(button, busy, busyText) {
    if (busy) { button.dataset.label = button.textContent; button.textContent = busyText; }
    else if (button.dataset.label) { button.textContent = button.dataset.label; }
    button.disabled = busy;
  }

  // Shared with login.js and register.js.
  R.authHelpers = { setBusy, REQUIRED, PASSWORD_HELP, EMAIL_RE, NAME_RE, passwordOk, mobileDigits };

  // ------------------------------------------------------------------ reset password
  function initReset() {
    const form = $("#reset-form");
    const sendBtn = $("#send-code");
    const submit = $("#submit-btn");
    let timer = null;

    function identifierError(value) {
      if (!value) return REQUIRED;
      if (value.includes("@")) return EMAIL_RE.test(value) ? "" : "Use your school email (name@g.batstate-u.edu.ph).";
      return mobileDigits(value) ? "" : "Enter your registered mobile number in +63 format.";
    }

    function startCooldown(seconds) {
      clearInterval(timer);
      let left = seconds;
      sendBtn.disabled = true;
      const tick = () => {
        if (left <= 0) {
          clearInterval(timer);
          sendBtn.disabled = false;
          sendBtn.textContent = "Send Code";
          return;
        }
        sendBtn.textContent = `Resend in ${left}s`;
        left -= 1;
      };
      tick();
      timer = setInterval(tick, 1000);
    }

    sendBtn.addEventListener("click", async () => {
      R.clearErrors(form);
      R.alert(alertBox(), "");
      const identifier = form.elements.identifier.value.trim();
      const err = identifierError(identifier);
      if (err) return R.showErrors(form, { identifier: err });

      sendBtn.disabled = true;
      sendBtn.textContent = "Sending…";
      const res = await R.api.post("/api/auth/reset/send-code/", { identifier });
      if (res.ok) {
        R.alert(alertBox(), res.message, "success");
        startCooldown(res.cooldown || 60);
        form.elements.code.focus();
        return;
      }
      if (res.status === 429 && res.retry_after) startCooldown(Math.min(res.retry_after, 3600));
      else { sendBtn.disabled = false; sendBtn.textContent = "Send Code"; }
      R.showErrors(form, res.errors);
      if (!Object.keys(res.errors || {}).length) R.alert(alertBox(), res.message || "We couldn't send the code.");
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      R.clearErrors(form);
      R.alert(alertBox(), "");
      const identifier = form.elements.identifier.value.trim();
      const code = form.elements.code.value.trim();
      const errors = {};
      const idErr = identifierError(identifier);
      if (idErr) errors.identifier = idErr;
      if (!code) errors.code = REQUIRED;
      if (Object.keys(errors).length) return R.showErrors(form, errors);

      setBusy(submit, true, "Checking…");
      const res = await R.api.post("/api/auth/reset/verify-code/", { identifier, code });
      if (res.ok) { window.location.href = res.redirect || "/change-password/"; return; }
      setBusy(submit, false);
      R.showErrors(form, res.errors);
      if (!Object.keys(res.errors || {}).length) R.alert(alertBox(), res.message || "We couldn't verify the code.");
    });
  }

  // ------------------------------------------------------------------ choose new password
  function initChange() {
    const form = $("#change-form");
    const submit = $("#submit-btn");

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      R.clearErrors(form);
      R.alert(alertBox(), "");
      const password = form.elements.password.value;
      const confirm = form.elements.confirm_password.value;
      const errors = {};
      if (!password) errors.password = REQUIRED;
      else if (!passwordOk(password)) errors.password = PASSWORD_HELP;
      if (!confirm) errors.confirm_password = REQUIRED;
      else if (password !== confirm) errors.confirm_password = "Passwords do not match.";
      if (Object.keys(errors).length) return R.showErrors(form, errors);

      setBusy(submit, true, "Saving…");
      const res = await R.api.post("/api/auth/reset/change-password/", { password, confirm_password: confirm });
      if (res.ok) {
        R.alert(alertBox(), res.message, "success");
        setTimeout(() => { window.location.href = res.redirect || "/login/"; }, 1800);
        return;
      }
      if (res.redirect) { // reset session expired
        R.alert(alertBox(), res.message);
        setTimeout(() => { window.location.href = res.redirect; }, 1800);
        return;
      }
      setBusy(submit, false);
      R.showErrors(form, res.errors);
      if (!Object.keys(res.errors || {}).length) R.alert(alertBox(), res.message || "We couldn't change your password.");
    });
  }

  R.bindEyes(document);
  const page = document.body.dataset.page;
  ({ reset: initReset, change: initChange }[page] || function () {})();
})();
