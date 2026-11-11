/* Log in: 1 email and password, 2 face scan (or an emailed code when the scan can't be used). */
(function () {
  const R = window.REUSE;
  const { setBusy, REQUIRED, EMAIL_RE } = R.authHelpers;
  const $ = (id) => document.getElementById(id);
  const alertBox = $("form-alert");
  const form = $("login-form");
  const panels = { password: form, face: $("face-step"), code: $("code-step"), enroll: $("enroll-step") };
  const video = $("scan-video");
  const prompt = $("scan-prompt");
  const scanBtn = $("scan-start");

  function show(name) {
    Object.entries(panels).forEach(([key, el]) => { el.hidden = key !== name; });
    if (name !== "face") stopCamera();
    const first = panels[name].querySelector("input:not([type=hidden]), button");
    if (first && name !== "password") first.focus();
  }

  function stopCamera() {
    R.faceScan.close(video);
    prompt.hidden = true;
    $("scan-placeholder").hidden = false;
  }

  // Every answer from the sign-in API says what comes next. Returns true when it was handled.
  function follow(res) {
    if (res.redirect) {
      window.location.href = res.ok ? res.redirect : res.redirect + "?timeout=1";
      return true;
    }
    if (res.next === "face_scan") { scanBtn.textContent = "Start Face Scan"; show("face"); return true; }
    if (res.next === "enroll_id") { show("enroll"); return true; }
    if (res.next === "email_code") {
      show("code");
      R.alert(alertBox, res.message || "We sent a sign-in code to your school email.", "info");
      return true;
    }
    return false;
  }

  // ------------------------------------------------------------------ step 1: password
  const params = new URLSearchParams(location.search);
  const resendWrap = $("resend-wrap");
  if (params.get("verified")) R.alert(alertBox, "Your account has been successfully verified. You can now use REUSE.", "success");
  else if (params.get("registered")) R.alert(alertBox, "A verification link has been sent to your school email.", "success");
  else if (params.get("pwchanged")) R.alert(alertBox, "Your password has been changed successfully.", "success");
  else if (params.get("timeout")) R.alert(alertBox, "Your sign-in timed out. Please log in again.", "info");
  else if (params.get("verify_error")) {
    R.alert(alertBox, "This verification link is invalid or has expired. Enter your email below to get a new one.", "error");
    resendWrap.hidden = false;
  }

  $("resend-link").addEventListener("click", async () => {
    const email = form.elements.email.value.trim();
    if (!EMAIL_RE.test(email)) {
      R.showErrors(form, { email: email ? "Use your school email (name@g.batstate-u.edu.ph)." : REQUIRED });
      return;
    }
    const res = await R.api.post("/api/auth/resend-verification/", { email });
    R.alert(alertBox, res.message, res.ok ? "success" : "error");
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const submit = $("submit-btn");
    R.clearErrors(form);
    R.alert(alertBox, "");
    resendWrap.hidden = true;

    const email = form.elements.email.value.trim();
    const password = form.elements.password.value;
    const errors = {};
    if (!email) errors.email = REQUIRED;
    if (!password) errors.password = REQUIRED;
    if (Object.keys(errors).length) return R.showErrors(form, errors);

    setBusy(submit, true, "Logging in…");
    const res = await R.api.post("/api/auth/login/", { email, password });
    if (res.ok && follow(res)) {
      if (!res.redirect) { setBusy(submit, false); form.elements.password.value = ""; }
      return;
    }
    setBusy(submit, false);
    R.showErrors(form, res.errors);
    if (!Object.keys(res.errors || {}).length) R.alert(alertBox, res.message || "We couldn't log you in. Please try again.");
    if (res.code === "unverified") resendWrap.hidden = false;
  });

  // ------------------------------------------------------------------ step 2: face scan
  function triesLeft(res) {
    if (res.code !== "not_confirmed" || !res.attempts_left) return "";
    return ` ${res.attempts_left} ${res.attempts_left === 1 ? "try" : "tries"} left.`;
  }

  scanBtn.addEventListener("click", async () => {
    R.alert(alertBox, "");
    setBusy(scanBtn, true, "Starting camera…");
    try {
      await R.faceScan.open(video);
    } catch (err) {
      stopCamera();
      setBusy(scanBtn, false);
      R.alert(alertBox, R.faceScan.cameraErrorText(err));
      $("use-code-wrap").hidden = false; // nobody should be stuck because of a missing camera
      return;
    }
    $("scan-placeholder").hidden = true;

    const challenge = await R.api.post("/api/auth/face-scan/", {});
    if (!challenge.ok || !challenge.steps) {
      stopCamera();
      setBusy(scanBtn, false);
      if (!follow(challenge)) R.alert(alertBox, challenge.message || "We couldn't start the face scan. Please try again.");
      return;
    }
    scanBtn.textContent = "Scanning…";
    prompt.hidden = false;
    const frames = await R.faceScan.capture(video, $("scan-canvas"), challenge, (text) => { prompt.textContent = text; });
    prompt.textContent = "Checking…";
    const res = await R.api.post("/api/auth/face-verify/", { challenge_id: challenge.challenge_id, frames });
    stopCamera();
    setBusy(scanBtn, false);
    if (res.ok) {
      R.alert(alertBox, "It's you. Logging in…", "success");
      follow(res);
      return;
    }
    if (follow(res)) return; // switched to the email code, or the sign-in timed out
    scanBtn.textContent = "Try Again";
    R.alert(alertBox, (res.message || "We couldn't check your face. Please try again.") + triesLeft(res));
  });

  $("use-code").addEventListener("click", async () => {
    const res = await R.api.post("/api/auth/login-code/send/", {});
    if (follow(res)) return;
    if (res.ok || res.status === 429) { // 429: a code was already sent a moment ago
      show("code");
      R.alert(alertBox, res.ok ? res.message : "We already sent you a code. Check your school email.", "info");
      return;
    }
    R.alert(alertBox, res.message || "We couldn't send the code. Please try again.");
  });

  // ------------------------------------------------------------------ fallback: emailed code
  const codeForm = panels.code;
  codeForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const submit = $("code-submit");
    R.clearErrors(codeForm);
    R.alert(alertBox, "");
    const code = codeForm.elements.code.value.trim();
    if (!code) return R.showErrors(codeForm, { code: REQUIRED });
    setBusy(submit, true, "Checking…");
    const res = await R.api.post("/api/auth/login-code/verify/", { code });
    if (follow(res)) return;
    setBusy(submit, false);
    R.showErrors(codeForm, res.errors);
    if (!Object.keys(res.errors || {}).length) R.alert(alertBox, res.message || "We couldn't check the code. Please try again.");
  });

  $("code-resend").addEventListener("click", async () => {
    const button = $("code-resend");
    button.disabled = true;
    const res = await R.api.post("/api/auth/login-code/send/", {});
    if (follow(res)) return;
    R.alert(alertBox, res.message || "We couldn't send the code. Please try again.", res.ok ? "success" : "error");
    setTimeout(() => { button.disabled = false; }, 1000 * Math.min(res.cooldown || res.retry_after || 5, 60));
  });

  // ------------------------------------------------------------------ accounts without a face on file
  const enrollForm = panels.enroll;
  enrollForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const submit = $("enroll-submit");
    R.clearErrors(enrollForm);
    R.alert(alertBox, "");
    const file = $("enroll-front").files[0];
    const errors = {};
    if (R.idUpload.fileProblem(file)) errors.file = R.idUpload.fileProblem(file);
    if (!$("enroll-consent").checked) errors.consent = "Please agree before continuing.";
    if (Object.keys(errors).length) return R.showErrors(enrollForm, errors);

    setBusy(submit, true, "Reading your ID…");
    let res = await R.idUpload.send(file, null);
    if (res.ok) res = await R.api.post("/api/auth/enroll-id/", { consent: true });
    setBusy(submit, false);
    if (res.ok && follow(res)) {
      R.alert(alertBox, "Your school ID was added. Now confirm it's you with the camera.", "success");
      return;
    }
    if (!res.ok && follow(res)) return;
    R.showErrors(enrollForm, res.errors);
    R.alert(alertBox, res.message || "We couldn't read your ID. Please try again.");
  });

  window.addEventListener("pagehide", stopCamera);
  R.bindEyes(document);
})();
