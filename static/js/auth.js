/* Login, create account, reset password, choose new password. */
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

  // ------------------------------------------------------------------ login
  function initLogin() {
    const form = $("#login-form");
    const submit = $("#submit-btn");
    const params = new URLSearchParams(location.search);
    const resendWrap = $("#resend-wrap");

    if (params.get("verified")) R.alert(alertBox(), "Your account has been successfully verified. You can now use REUSE.", "success");
    else if (params.get("registered")) R.alert(alertBox(), "A verification link has been sent to your school email.", "success");
    else if (params.get("pwchanged")) R.alert(alertBox(), "Your password has been changed successfully.", "success");
    else if (params.get("verify_error")) {
      R.alert(alertBox(), "This verification link is invalid or has expired. Enter your email below to get a new one.", "error");
      resendWrap.hidden = false;
    }

    $("#resend-link").addEventListener("click", async () => {
      const email = form.elements.email.value.trim();
      if (!EMAIL_RE.test(email)) {
        R.showErrors(form, { email: email ? "Use your school email (name@g.batstate-u.edu.ph)." : REQUIRED });
        return;
      }
      const res = await R.api.post("/api/auth/resend-verification/", { email });
      R.alert(alertBox(), res.message, res.ok ? "success" : "error");
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      R.clearErrors(form);
      R.alert(alertBox(), "");
      resendWrap.hidden = true;

      const email = form.elements.email.value.trim();
      const password = form.elements.password.value;
      const errors = {};
      if (!email) errors.email = REQUIRED;
      else if (!EMAIL_RE.test(email)) errors.email = "Use your school email (name@g.batstate-u.edu.ph).";
      if (!password) errors.password = REQUIRED;
      if (Object.keys(errors).length) return R.showErrors(form, errors);

      setBusy(submit, true, "Logging in…");
      const res = await R.api.post("/api/auth/login/", { email, password });
      if (res.ok) { window.location.href = res.redirect || "/dashboard/"; return; }
      setBusy(submit, false);
      R.showErrors(form, res.errors);
      if (!Object.keys(res.errors || {}).length) R.alert(alertBox(), res.message || "We couldn't log you in. Please try again.");
      if (res.code === "unverified") resendWrap.hidden = false;
    });
  }

  // ------------------------------------------------------------------ create account
  function initCreate() {
    const form = $("#create-form");
    const submit = $("#submit-btn");

    // Digits only after the +63 prefix.
    form.elements.mobile.addEventListener("input", (e) => {
      let d = e.target.value.replace(/\D/g, "");
      if (d.startsWith("63") && d.length > 10) d = d.slice(2);
      if (d.startsWith("0")) d = d.slice(1);
      e.target.value = d.slice(0, 10);
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      R.clearErrors(form);
      R.alert(alertBox(), "");

      const v = (name) => form.elements[name].value;
      const fullName = v("full_name").normalize("NFC").replace(/\u2019/g, "'").replace(/\s+/g, " ").trim();
      const email = v("email").trim();
      const mobile = v("mobile").trim();
      const errors = {};

      if (!fullName) errors.full_name = REQUIRED;
      else if (!NAME_RE.test(fullName)) errors.full_name = "Use letters only. Spaces, apostrophes and hyphens are allowed.";
      else if (fullName.split(" ").length < 2) errors.full_name = "Enter your first name, middle name (if any) and last name.";

      if (!email) errors.email = REQUIRED;
      else if (!EMAIL_RE.test(email)) errors.email = "Use your school email (name@g.batstate-u.edu.ph).";

      if (!mobile) errors.mobile = REQUIRED;
      else if (!mobileDigits(mobile)) errors.mobile = "Enter a valid Philippine mobile number (+63 9XX XXX XXXX).";

      if (!v("user_type")) errors.user_type = REQUIRED;

      if (!v("password")) errors.password = REQUIRED;
      else if (!passwordOk(v("password"))) errors.password = PASSWORD_HELP;
      if (!v("confirm_password")) errors.confirm_password = REQUIRED;
      else if (v("password") !== v("confirm_password")) errors.confirm_password = "Passwords do not match.";

      if (Object.keys(errors).length) return R.showErrors(form, errors);

      setBusy(submit, true, "Creating account…");
      const res = await R.api.post("/api/auth/register/", {
        full_name: fullName, email, mobile: "+63" + mobileDigits(mobile),
        user_type: v("user_type"), password: v("password"), confirm_password: v("confirm_password"),
      });
      if (!res.ok) {
        setBusy(submit, false);
        R.showErrors(form, res.errors);
        R.alert(alertBox(), res.message || "We couldn't create your account. Please try again.");
        return;
      }
      // Stay on the button as "busy" and move to the login page after the message is read.
      R.alert(alertBox(), res.message, res.email_sent === false ? "info" : "success");
      window.scrollTo({ top: 0, behavior: "smooth" });
      setTimeout(() => { window.location.href = res.email_sent === false ? "/login/" : "/login/?registered=1"; }, 2500);
    });
  }

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
  ({ login: initLogin, create: initCreate, reset: initReset, change: initChange }[page] || function () {})();
})();
