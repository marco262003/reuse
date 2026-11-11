/* Create an account: 1 upload the school ID, 2 details (autofilled from the ID), 3 review. */
(function () {
  const R = window.REUSE;
  const { esc } = R;
  const { setBusy, REQUIRED, PASSWORD_HELP, EMAIL_RE, NAME_RE, passwordOk, mobileDigits } = R.authHelpers;
  const $ = (id) => document.getElementById(id);

  // The field list comes from core/registration_fields.py: it alone says what autofill may touch.
  const schema = JSON.parse($("registration-schema").textContent);
  const idNames = R.autofill.idFieldNames(schema);
  const form = $("create-form");
  const alertBox = $("form-alert");
  const panels = { id: $("step-id"), details: form, review: $("step-review") };
  const ORDER = ["id", "details", "review"];
  const LOW_CONFIDENCE = 0.6;

  let autofilled = {}; // what autofill last wrote, by field name
  let unsure = {};     // fields the ID read was not confident about
  let idRead = false;  // a usable ID has been read for this sign-up

  const input = (name) => form.elements[name];
  const text = (name) => input(name).value.trim();

  function show(step) {
    ORDER.forEach((name, i) => {
      panels[name].hidden = name !== step;
      const marker = document.querySelector(`#reg-steps [data-step="${name}"]`);
      marker.classList.toggle("is-current", name === step);
      marker.classList.toggle("is-done", i < ORDER.indexOf(step));
      if (name === step) marker.setAttribute("aria-current", "step"); else marker.removeAttribute("aria-current");
    });
    $("id-back-to-form").hidden = !idRead;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ------------------------------------------------------------------ badges on the form
  function refreshBadge(name) {
    const label = form.querySelector(`[data-field="${name}"] .input-label`);
    if (!label) return;
    label.querySelectorAll(".id-badge").forEach((el) => el.remove());
    const where = R.autofill.origin(name, text(name), autofilled);
    if (where !== "read" || !text(name)) return;
    const badge = document.createElement("span");
    badge.className = "id-badge" + (unsure[name] ? " is-check" : "");
    badge.textContent = unsure[name] ? "Read from your ID: please check" : "Read from your ID";
    label.appendChild(badge);
  }

  idNames.forEach((name) => {
    input(name).addEventListener("input", () => refreshBadge(name));
    input(name).addEventListener("change", () => refreshBadge(name));
  });

  // Digits only after the +63 prefix.
  input("mobile").addEventListener("input", (e) => {
    let d = e.target.value.replace(/\D/g, "");
    if (d.startsWith("63") && d.length > 10) d = d.slice(2);
    if (d.startsWith("0")) d = d.slice(1);
    e.target.value = d.slice(0, 10);
  });

  // ------------------------------------------------------------------ step 1: the ID
  async function useExtraction(res) {
    const current = Object.fromEntries(idNames.map((name) => [name, text(name)]));
    let result = R.autofill.plan(schema, current, autofilled, res.fields, false);
    if (result.conflicts.length) {
      const replace = await R.confirm({
        title: "This ID has different details",
        message: "This ID contains different information than what's on the form. Replace your edits, or keep them?",
        confirmText: "Replace my edits", cancelText: "Keep my edits",
      });
      if (replace) result = R.autofill.plan(schema, current, autofilled, res.fields, true);
    }
    Object.entries(result.writes).forEach(([name, value]) => {
      input(name).value = value;
      if (input(name).value === value) autofilled[name] = value; // a select only takes one of its options
    });
    result.clears.forEach((name) => { input(name).value = ""; delete autofilled[name]; });
    unsure = {};
    Object.entries(res.fields || {}).forEach(([name, item]) => { if (item.confidence < LOW_CONFIDENCE) unsure[name] = true; });
    idNames.forEach(refreshBadge);

    const filled = idNames.filter((name) => R.autofill.origin(name, text(name), autofilled) === "read" && text(name)).length;
    const notes = (res.warnings || []).slice();
    if (filled) notes.unshift(`We filled in ${filled} ${filled === 1 ? "field" : "fields"} from your ID. You can change any of them.`);
    R.alert($("extract-note"), notes.join(" "), "info");
    idRead = true;
    R.alert(alertBox, "");
    show("details");
  }

  $("id-continue").addEventListener("click", async () => {
    const button = $("id-continue");
    const front = $("id-front").files[0];
    const back = $("id-back").files[0];
    R.clearErrors(panels.id);
    R.alert(alertBox, "");
    const errors = {};
    const frontProblem = R.idUpload.fileProblem(front);
    if (frontProblem) errors.file = frontProblem;
    if (back && R.idUpload.fileProblem(back)) errors.back = R.idUpload.fileProblem(back);
    if (Object.keys(errors).length) return R.showErrors(panels.id, errors);

    setBusy(button, true, "Reading your ID…");
    const res = await R.idUpload.send(front, back);
    setBusy(button, false);
    if (!res.ok) {
      R.showErrors(panels.id, res.errors);
      R.alert(alertBox, res.message || "We couldn't read your ID. Please try again.");
      return;
    }
    await useExtraction(res);
  });

  $("change-id").addEventListener("click", () => { R.alert(alertBox, ""); show("id"); });
  $("keep-id").addEventListener("click", () => { R.alert(alertBox, ""); show("details"); });

  // ------------------------------------------------------------------ step 2: details
  function isRequired(field) {
    return field.required || (field.required_for || []).includes(text("user_type"));
  }

  function checkForm() {
    const errors = {};
    schema.forEach((field) => {
      if (field.kind !== "checkbox" && field.kind !== "password" && isRequired(field) && !text(field.name)) errors[field.name] = REQUIRED;
    });
    ["first_name", "middle_name", "last_name"].forEach((name) => {
      if (text(name) && !NAME_RE.test(text(name).normalize("NFC").replace(/’/g, "'").replace(/\s+/g, " "))) {
        errors[name] = "Use letters only. Spaces, apostrophes and hyphens are allowed.";
      }
    });
    if (text("email") && !EMAIL_RE.test(text("email"))) errors.email = "Use your school email (name@g.batstate-u.edu.ph).";
    if (text("mobile") && !mobileDigits(text("mobile"))) errors.mobile = "Enter a valid Philippine mobile number (+63 9XX XXX XXXX).";
    const password = input("password").value;
    const confirm = input("confirm_password").value;
    if (!password) errors.password = REQUIRED;
    else if (!passwordOk(password)) errors.password = PASSWORD_HELP;
    if (!confirm) errors.confirm_password = REQUIRED;
    else if (password !== confirm) errors.confirm_password = "Passwords do not match.";
    if (!input("consent").checked) errors.consent = "Please agree before continuing.";
    return errors;
  }

  function payload() {
    const data = {};
    schema.forEach((field) => {
      if (field.kind === "checkbox") data[field.name] = input(field.name).checked;
      else if (field.kind === "password") data[field.name] = input(field.name).value;
      else data[field.name] = text(field.name);
    });
    data.mobile = "+63" + mobileDigits(data.mobile);
    return data;
  }

  // ------------------------------------------------------------------ step 3: review
  const ORIGIN_BADGE = {
    read: '<span class="id-badge">Read from your ID</span>',
    edited: '<span class="id-badge is-check">Read from your ID, changed by you</span>',
    typed: '<span class="id-badge is-typed">Typed by you</span>',
  };

  function shown(field) {
    const el = input(field.name);
    if (field.kind === "password") return "Hidden";
    if (field.kind === "checkbox") return el.checked ? "Agreed" : "Not agreed";
    if (field.kind === "select") return el.value ? el.options[el.selectedIndex].text : "";
    if (field.name === "mobile") return text("mobile") ? "+63 " + text("mobile") : "";
    return text(field.name);
  }

  function reviewGroup(title, fields, badge) {
    const rows = fields.filter((field) => shown(field)).map((field) =>
      `<div><dt>${esc(field.label.replace(" (optional)", ""))}</dt><dd>${esc(shown(field))}${badge(field)}</dd></div>`);
    return `<section class="review-group"><h3>${esc(title)}</h3><dl class="review-list">${rows.join("")}</dl></section>`;
  }

  function buildReview() {
    const fromId = schema.filter((field) => field.source === "id");
    const manual = schema.filter((field) => field.source !== "id" && field.name !== "confirm_password");
    $("review-list").innerHTML =
      reviewGroup("From your school ID", fromId, (field) => ORIGIN_BADGE[R.autofill.origin(field.name, text(field.name), autofilled)]) +
      reviewGroup("About your account", manual, () => "");
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    R.clearErrors(form);
    R.alert(alertBox, "");
    const errors = checkForm();
    if (Object.keys(errors).length) {
      R.showErrors(form, errors);
      R.alert(alertBox, "Please fix the highlighted fields.");
      return;
    }
    buildReview();
    show("review");
  });

  $("edit-btn").addEventListener("click", () => show("details"));

  $("submit-btn").addEventListener("click", async () => {
    const button = $("submit-btn");
    R.alert(alertBox, "");
    setBusy(button, true, "Creating account…");
    const res = await R.api.post("/api/auth/register/", payload());
    if (!res.ok) {
      setBusy(button, false);
      R.alert(alertBox, res.message || "We couldn't create your account. Please try again.");
      if (res.code === "no_reference_face" || res.code === "duplicate_id") {
        idRead = false;
        show("id"); // the ID has to be added again
        return;
      }
      show("details");
      R.showErrors(form, res.errors);
      return;
    }
    // Stay "busy" and move to the login page after the message is read.
    R.alert(alertBox, res.message, res.email_sent === false ? "info" : "success");
    window.scrollTo({ top: 0, behavior: "smooth" });
    setTimeout(() => { window.location.href = res.email_sent === false ? "/login/" : "/login/?registered=1"; }, 2500);
  });

  R.bindEyes(document);
  show("id");
})();
