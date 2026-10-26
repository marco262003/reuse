(function () {
  const R = window.REUSE;
  const { esc } = R;
  const PASSWORD_HELP = "Use at least 8 characters, with an uppercase letter, a number and a special character.";
  const passwordOk = (p) => p.length >= 8 && /[A-Z]/.test(p) && /\d/.test(p) && /[^A-Za-z0-9]/.test(p);

  R.userReady.then((user) => {
    if (!user) return;
    document.getElementById("p-name").textContent = user.full_name;
    document.getElementById("p-type").textContent = user.user_type_label;
    document.getElementById("p-email").textContent = user.email;
    document.getElementById("p-mobile").textContent = user.mobile || "Not set";
    if (user.profile_image_url) document.getElementById("profile-photo-preview").src = user.profile_image_url;
  });

  const photoForm = document.getElementById("profile-photo-form");
  const photoInput = document.getElementById("profile-photo-input");
  const photoPreview = document.getElementById("profile-photo-preview");
  const photoSave = document.getElementById("profile-photo-save");
  const photoError = document.getElementById("profile-photo-error");
  let selectedPhoto = null;
  photoInput.addEventListener("change", () => {
    selectedPhoto = photoInput.files && photoInput.files[0];
    photoError.textContent = "";
    if (!selectedPhoto) { photoSave.disabled = true; return; }
    if (selectedPhoto.size > 5 * 1024 * 1024) {
      photoError.textContent = "Photo must be 5 MB or smaller.";
      photoInput.value = ""; selectedPhoto = null; photoSave.disabled = true; return;
    }
    if (! ["image/jpeg", "image/png", "image/webp"].includes(selectedPhoto.type)) {
      photoError.textContent = "Upload a JPG, PNG, or WEBP image.";
      photoInput.value = ""; selectedPhoto = null; photoSave.disabled = true; return;
    }
    photoPreview.src = URL.createObjectURL(selectedPhoto);
    photoSave.disabled = false;
  });
  photoForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!selectedPhoto) return;
    photoSave.disabled = true; photoSave.textContent = "Uploading…"; photoError.textContent = "";
    const match = document.cookie.match(/(?:^|; )csrftoken=([^;]*)/);
    const data = new FormData(); data.append("image", selectedPhoto);
    try {
      const response = await fetch("/api/me/profile-image/", {method:"POST", credentials:"same-origin", headers:{"X-CSRFToken":match ? decodeURIComponent(match[1]) : "", Accept:"application/json"}, body:data});
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.message || "Could not upload photo.");
      if (result.user.profile_image_url) photoPreview.src = result.user.profile_image_url;
      selectedPhoto = null; photoInput.value = ""; R.toast("Profile photo updated.", "success");
    } catch (err) { photoError.textContent = err.message || "Could not upload photo. Please try again."; }
    finally { photoSave.disabled = !selectedPhoto; photoSave.textContent = "Save photo"; }
  });

  const passwordField = (name, label, auto) => `
    <div class="form-field" data-field="${name}">
      <label for="cp-${name}">${esc(label)}</label>
      <div class="password-field"><input class="control" id="cp-${name}" name="${name}" type="password" autocomplete="${auto}">${R.eyeButton()}</div>
      <p class="field-error"></p>
    </div>`;

  document.getElementById("change-password-btn").addEventListener("click", () => {
    const modal = R.modal.open(
      `<form id="cp-form" novalidate>
         <h2 class="modal-title">Change Password</h2>
         ${passwordField("current_password", "Current password", "current-password")}
         ${passwordField("password", "New password", "new-password")}
         ${passwordField("confirm_password", "Confirm new password", "new-password")}
         <p class="muted">${PASSWORD_HELP}</p>
         <p class="form-alert is-error" id="cp-alert" role="alert" hidden></p>
         <div class="modal-actions"><button type="submit" class="btn btn-primary" id="cp-submit">Change Password</button></div>
       </form>`,
      { className: "modal-sm", label: "Change password" }
    );
    const form = modal.body.querySelector("#cp-form");
    const alertBox = form.querySelector("#cp-alert");
    const submit = form.querySelector("#cp-submit");

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      R.clearErrors(form);
      R.alert(alertBox, "");
      const current = form.elements.current_password.value;
      const password = form.elements.password.value;
      const confirm = form.elements.confirm_password.value;
      const errors = {};
      if (!current) errors.current_password = "Please fill out this field.";
      if (!password) errors.password = "Please fill out this field.";
      else if (!passwordOk(password)) errors.password = PASSWORD_HELP;
      if (!confirm) errors.confirm_password = "Please fill out this field.";
      else if (password !== confirm) errors.confirm_password = "Passwords do not match.";
      if (Object.keys(errors).length) return R.showErrors(form, errors);

      submit.disabled = true;
      submit.textContent = "Saving…";
      const res = await R.api.post("/api/me/change-password/", { current_password: current, password, confirm_password: confirm });
      submit.disabled = false;
      submit.textContent = "Change Password";
      if (res.ok) {
        modal.close();
        R.toast(res.message || "Password changed successfully.", "success");
        return;
      }
      R.showErrors(form, res.errors);
      if (!Object.keys(res.errors || {}).length) R.alert(alertBox, res.message || "We couldn't change your password.");
    });
  });

  document.getElementById("logout-btn").addEventListener("click", async () => {
    const yes = await R.confirm({
      title: "Log out of REUSE?",
      message: "You’ll need to log in again to request or return items.",
      confirmText: "Log out",
      cancelText: "Stay logged in",
    });
    if (!yes) return;
    const res = await R.api.post("/api/auth/logout/");
    if (res.ok) window.location.href = res.redirect || "/login/";
    else R.toast(res.message || "We couldn't log you out. Please try again.", "error");
  });
})();
