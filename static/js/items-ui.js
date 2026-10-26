/*
 * Pop-ups shared by several pages:
 *   item details -> request form -> "request submitted" -> edit / cancel
 *   request details, borrowing details, receipts
 */
(function () {
  const R = window.REUSE;
  const { esc, fmt } = R;

  const REASONS = [
    ["class_activity", "Class Activity"],
    ["school_project", "School Project"],
    ["org_event", "Organization Event"],
    ["faculty_staff", "Faculty/Staff Activity"],
    ["other", "Other"],
  ];

  const photo = (item) => `<img src="${esc(item.image_url || R.placeholder)}" alt="${esc(item.name)}">`;
  const row = (label, value) => `<dt>${esc(label)}</dt><dd>${value == null || value === "" ? "–" : value}</dd>`;
  const receiptChips = (receipts) =>
    `<div class="chips">${receipts.map((r) => `<button type="button" class="chip" data-receipt="${esc(r.id)}">${esc(r.type)} receipt ${esc(r.id)}</button>`).join("")}</div>`;

  function availability(item) {
    return item.is_available
      ? '<p class="avail avail-yes">AVAILABLE</p>'
      : `<p class="avail avail-no">UNAVAILABLE<span class="avail-reason">${esc(item.unavailable_reason)}</span></p>`;
  }

  // Click handling shared by every modal: receipt chips open the receipt.
  function wireReceipts(modal) {
    modal.body.addEventListener("click", (e) => {
      const chip = e.target.closest("[data-receipt]");
      if (chip) openReceipt(chip.dataset.receipt);
    });
  }

  // ---------------------------------------------------------------- item details
  async function openItem(itemId) {
    const res = await R.api.get(`/api/items/${itemId}/`);
    if (!res.ok) return R.toast(res.message || "We couldn't load this item.", "error");
    const item = res.item;

    let actions;
    if (item.is_available) {
      actions = item.pending_request_id
        ? '<button type="button" class="btn btn-primary btn-pending" data-act="pending">Pending</button>'
        : '<button type="button" class="btn btn-primary" data-act="request">Request Item</button>';
    } else {
      actions =
        `<button type="button" class="btn btn-primary btn-sm" data-act="notify" ${item.subscribed ? "disabled" : ""}>${item.subscribed ? "Notification On" : "Notify When Available"}</button>` +
        '<button type="button" class="btn btn-primary" disabled>Request Item</button>';
    }

    const modal = R.modal.open(
      `<div class="detail-layout">
         <div class="detail-media">${photo(item)}${availability(item)}</div>
         <dl class="kv">
           ${row("Item:", esc(item.name))}
           ${row("Category:", esc(item.category_label))}
           ${row("Description:", esc(item.description))}
           ${row("Quantity available:", item.available_qty)}
           ${row("Condition:", esc(item.condition_label))}
           ${row("Times borrowed:", item.times_borrowed)}
           ${row("Pickup/Return area:", esc(item.pickup_return_area))}
         </dl>
       </div>
       <div class="modal-actions">${actions}</div>`,
      { className: "modal-wide", label: item.name }
    );

    modal.body.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn) return;
      if (btn.dataset.act === "request") { modal.close(); openRequestForm(item); }
      if (btn.dataset.act === "pending") { modal.close(); openRequestDetails(item.pending_request_id); }
      if (btn.dataset.act === "notify") {
        btn.disabled = true;
        const result = await R.api.post(`/api/items/${item.id}/subscribe/`);
        if (!result.ok) {
          btn.disabled = false;
          return R.toast(result.message || "We couldn't turn on notifications.", "error");
        }
        modal.close();
        R.modal.open(
          `<h2 class="modal-title">Notification Enabled <span class="check" aria-hidden="true">✓</span></h2>
           <p class="modal-text">You’ll be notified through your registered email, mobile number and REUSE web notifications when this item becomes available.</p>`,
          { className: "modal-sm", label: "Notification enabled" }
        );
        R.changed();
      }
    });
  }

  // ---------------------------------------------------------------- request form (new + edit)
  function openRequestForm(item, existing = null) {
    const editing = Boolean(existing);
    const max = Math.max(1, item.available_qty);
    const qty = Math.min(existing ? existing.quantity : 1, max);
    const today = fmt.toDateInput(new Date());
    const v = existing
      ? {
          bd: fmt.toDateInput(existing.borrow_at), bt: fmt.toTimeInput(existing.borrow_at),
          rd: fmt.toDateInput(existing.return_at), rt: fmt.toTimeInput(existing.return_at),
          reason: existing.reason, other: existing.reason_other, details: existing.details,
        }
      : { bd: "", bt: "", rd: "", rt: "", reason: "", other: "", details: "" };

    const modal = R.modal.open(
      `<form id="request-form" novalidate>
         <h2 class="modal-title caps">Request “${esc(item.name)}”</h2>
         <div class="form-grid">
           <div class="form-field" data-field="borrow_date"><label for="rf-bd">Borrow Date</label>
             <input class="control" id="rf-bd" name="borrow_date" type="date" min="${today}" value="${esc(v.bd)}"><p class="field-error"></p></div>
           <div class="form-field" data-field="return_date"><label for="rf-rd">Return Date</label>
             <input class="control" id="rf-rd" name="return_date" type="date" min="${today}" value="${esc(v.rd)}"><p class="field-error"></p></div>
           <div class="form-field" data-field="borrow_time"><label for="rf-bt">Borrow Time</label>
             <input class="control" id="rf-bt" name="borrow_time" type="time" value="${esc(v.bt)}"><p class="field-error"></p></div>
           <div class="form-field" data-field="return_time"><label for="rf-rt">Return Time</label>
             <input class="control" id="rf-rt" name="return_time" type="time" value="${esc(v.rt)}"><p class="field-error"></p></div>
         </div>
         <div class="form-field" data-field="quantity"><label for="rf-qty">Quantity</label>
           <div class="qty-row"><input id="rf-qty" name="quantity" type="range" min="1" max="${max}" value="${qty}">
             <output for="rf-qty" id="rf-qty-out">${qty}/${max}</output></div><p class="field-error"></p></div>
         <div class="form-field" data-field="reason"><label for="rf-reason">Reason for Borrowing</label>
           <select class="control" id="rf-reason" name="reason"><option value="">Select a reason</option>
             ${REASONS.map(([val, label]) => `<option value="${val}" ${v.reason === val ? "selected" : ""}>${label}</option>`).join("")}
           </select><p class="field-error"></p></div>
         <div class="form-field" data-field="reason_other" id="rf-other-wrap" ${v.reason === "other" ? "" : "hidden"}><label for="rf-other">Specify your reason</label>
           <input class="control" id="rf-other" name="reason_other" maxlength="200" value="${esc(v.other)}"><p class="field-error"></p></div>
         <div class="form-field" data-field="details"><label for="rf-details">Additional details <span class="muted">(optional)</span></label>
           <textarea class="control" id="rf-details" name="details" rows="3" maxlength="500">${esc(v.details)}</textarea><p class="field-error"></p></div>
         <p class="form-alert is-error" id="rf-alert" role="alert" hidden></p>
         <div class="modal-actions"><button type="submit" class="btn btn-primary" id="rf-submit">${editing ? "Save Changes" : "Submit Request"}</button></div>
       </form>`,
      { label: `Request ${item.name}` }
    );

    const form = modal.body.querySelector("#request-form");
    const alertBox = form.querySelector("#rf-alert");
    const slider = form.elements.quantity;
    slider.addEventListener("input", () => { form.querySelector("#rf-qty-out").textContent = `${slider.value}/${max}`; });
    form.elements.reason.addEventListener("change", (e) => {
      form.querySelector("#rf-other-wrap").hidden = e.target.value !== "other";
    });

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      R.clearErrors(form);
      R.alert(alertBox, "");
      const body = {
        item_id: item.id,
        quantity: Number(slider.value),
        borrow_date: form.elements.borrow_date.value, borrow_time: form.elements.borrow_time.value,
        return_date: form.elements.return_date.value, return_time: form.elements.return_time.value,
        reason: form.elements.reason.value, reason_other: form.elements.reason_other.value.trim(),
        details: form.elements.details.value.trim(),
      };

      // Quick checks for a faster answer. The server repeats every check.
      const errors = {};
      ["borrow_date", "borrow_time", "return_date", "return_time"].forEach((k) => { if (!body[k]) errors[k] = "Please fill out this field."; });
      if (!body.reason) errors.reason = "Select a reason for borrowing.";
      if (body.reason === "other" && body.reason_other.length < 3) errors.reason_other = "Tell us the specific reason.";
      if (!errors.borrow_date && !errors.borrow_time && !errors.return_date && !errors.return_time) {
        if (new Date(`${body.return_date}T${body.return_time}`) <= new Date(`${body.borrow_date}T${body.borrow_time}`)) {
          errors.return_date = "The return date and time must be after the borrow date and time.";
        }
      }
      if (Object.keys(errors).length) { R.showErrors(form, errors); return; }

      const submit = form.querySelector("#rf-submit");
      const label = submit.textContent;
      submit.disabled = true;
      submit.textContent = "Saving…";
      const res = editing
        ? await R.api.patch(`/api/requests/${existing.id}/`, body)
        : await R.api.post("/api/requests/", body);
      submit.disabled = false;
      submit.textContent = label;

      if (!res.ok) {
        R.showErrors(form, res.errors);
        R.alert(alertBox, res.message || "We couldn't save your request. Please try again.");
        return;
      }
      modal.close();
      openSubmitted(res.request, editing ? "updated" : "submitted");
      R.changed();
    });
  }

  // ---------------------------------------------------------------- cancel
  async function cancelRequest(req) {
    const approved = req.status === "approved";
    const yes = await R.confirm({
      title: approved ? "Cancel this reservation?" : "Cancel this request?",
      message: approved
        ? "The reserved quantity will be released. To borrow this item again, you’ll need to submit a new request."
        : "This request will be canceled and kept in your history.",
      confirmText: approved ? "Cancel reservation" : "Cancel request",
      cancelText: "Keep it",
    });
    if (!yes) return false;
    const res = await R.api.post(`/api/requests/${req.id}/cancel/`);
    if (!res.ok) { R.toast(res.message || "We couldn't cancel this request.", "error"); return false; }
    R.toast(approved ? "Reservation canceled." : "Request canceled.", "success");
    R.changed();
    return true;
  }

  // ---------------------------------------------------------------- "Request Submitted!"
  function openSubmitted(req, mode = "submitted") {
    const modal = R.modal.open(
      `<h2 class="modal-title">Request ${mode === "updated" ? "Updated" : "Submitted"}! <span class="check" aria-hidden="true">✓</span></h2>
       <p class="modal-text">Your request has been ${mode} successfully.</p>
       <p class="modal-text">Request ID: <strong>${esc(req.id)}</strong></p>
       <p class="modal-text">Status: ${R.badge(req.status, req.status_label)}</p>
       <p><button type="button" class="link-btn" data-receipt="${esc(req.id)}">View request receipt</button></p>
       <div class="modal-actions">
         <button type="button" class="btn btn-outline" data-act="cancel">Cancel Request</button>
         <button type="button" class="btn btn-primary" data-act="edit">Edit Request</button>
       </div>`,
      { className: "modal-sm", label: "Request submitted" }
    );
    wireReceipts(modal);
    modal.body.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn) return;
      if (btn.dataset.act === "cancel") { if (await cancelRequest(req)) modal.close(); }
      if (btn.dataset.act === "edit") {
        const res = await R.api.get(`/api/items/${req.item.id}/`);
        if (!res.ok) return R.toast(res.message || "We couldn't load this item.", "error");
        modal.close();
        openRequestForm(res.item, req);
      }
    });
  }

  // ---------------------------------------------------------------- request details
  async function openRequestDetails(requestId) {
    const first = await R.api.get(`/api/requests/${requestId}/`);
    if (!first.ok) return R.toast(first.message || "We couldn't load this request.", "error");
    const req = first.request;
    const itemRes = await R.api.get(`/api/items/${req.item.id}/`);
    const item = itemRes.ok ? itemRes.item : null;

    const rejected = req.status === "rejected" && req.rejection_reason
      ? `<div class="notice notice-error"><strong>Reason for rejection:</strong> ${esc(req.rejection_reason)}</div>` : "";
    const approvedNote = req.status === "approved"
      ? '<div class="notice notice-info">This reservation can’t be edited. To change it, cancel it first, then submit a new request.</div>' : "";

    let actions = "";
    if (req.status === "pending") {
      actions = '<button type="button" class="btn btn-outline" data-act="cancel">Cancel Request</button><button type="button" class="btn btn-primary" data-act="edit">Edit Request</button>';
    } else if (req.status === "approved") {
      actions = '<button type="button" class="btn btn-outline" data-act="cancel">Cancel Reservation</button>';
    }

    const modal = R.modal.open(
      `<div class="detail-layout">
         <div class="detail-media">${photo(req.item)}<p class="avail" style="text-align:center">${R.badge(req.status, req.status_label)}</p></div>
         <dl class="kv">
           ${row("Request ID:", esc(req.id))}
           ${row("Item:", esc(req.item.name))}
           ${row("Category:", esc(req.item.category_label))}
           ${item ? row("Description:", esc(item.description)) : ""}
           ${item ? row("Condition:", esc(item.condition_label)) : ""}
           ${item ? row("Pickup/Return area:", esc(item.pickup_return_area)) : ""}
           ${row("Quantity requested:", req.quantity)}
           ${row("Requested on:", fmt.long(req.created_at))}
           ${row("Borrow:", fmt.long(req.borrow_at))}
           ${row("Return:", fmt.long(req.return_at))}
           ${row("Reason for borrowing:", esc(req.purpose))}
           ${row("Status:", esc(req.status_label))}
           ${row("Receipts:", receiptChips(req.receipts))}
         </dl>
       </div>
       ${rejected}${approvedNote}
       ${actions ? `<div class="modal-actions">${actions}</div>` : ""}`,
      { className: "modal-wide", label: `Request ${req.id}` }
    );
    wireReceipts(modal);
    modal.body.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-act]");
      if (!btn) return;
      if (btn.dataset.act === "cancel") { if (await cancelRequest(req)) modal.close(); }
      if (btn.dataset.act === "edit") {
        const res = await R.api.get(`/api/items/${req.item.id}/`);
        if (!res.ok) return R.toast(res.message || "We couldn't load this item.", "error");
        modal.close();
        openRequestForm(res.item, req);
      }
    });
  }

  // ---------------------------------------------------------------- borrowing details
  async function openBorrowing(borrowingId) {
    const res = await R.api.get(`/api/borrowings/${borrowingId}/`);
    if (!res.ok) return R.toast(res.message || "We couldn't load this borrowing.", "error");
    const b = res.borrowing;
    const modal = R.modal.open(
      `<div class="detail-layout">
         <div class="detail-media">${photo(b.item)}</div>
         <dl class="kv">
           ${row("Borrowing ID:", esc(b.id))}
           ${row("Item Name:", esc(b.item.name))}
           ${row("Category:", esc(b.item.category_label))}
           ${row("Quantity:", b.quantity)}
           ${row("Borrowed on:", fmt.long(b.picked_up_at))}
           ${row("Due on:", fmt.long(b.due_at))}
           ${b.returned_at ? row("Returned on:", fmt.long(b.returned_at)) : ""}
           ${row("Purpose:", esc(b.purpose))}
           ${row("Pickup Location:", esc(b.pickup_area))}
           ${row("Return Location:", esc(b.return_area))}
           ${row("Status:", R.badge(b.status, b.status_label))}
           ${row("Receipts:", receiptChips(b.receipts))}
         </dl>
       </div>`,
      { className: "modal-wide", label: `Borrowing ${b.id}` }
    );
    wireReceipts(modal);
  }

  // A history card may be a returned borrowing or a canceled reservation.
  function openRecord(kind, id) {
    return kind === "reservation" ? openRequestDetails(id) : openBorrowing(id);
  }

  // ---------------------------------------------------------------- receipt
  async function openReceipt(refId) {
    const res = await R.api.get(`/api/receipts/${refId}/`);
    if (!res.ok) return R.toast(res.message || "We couldn't load this receipt.", "error");
    const r = res.receipt;
    const modal = R.modal.open(
      `<article>
         <h2 class="modal-title">${esc(r.type)} Receipt ${esc(r.id)}</h2>
         <dl class="kv">
           ${row("Request ID:", esc(r.request_id))}
           ${r.approval_id ? row("Approval ID:", esc(r.approval_id)) : ""}
           ${r.borrowing_id ? row("Borrowing ID:", esc(r.borrowing_id)) : ""}
           ${row("Item:", esc(r.item))}
           ${row("Quantity:", r.quantity)}
           ${row("Requester:", `${esc(r.requester.name)} (${esc(r.requester.user_type)})<br>${esc(r.requester.email)}`)}
           ${row("Requested on:", fmt.long(r.requested_at))}
           ${r.approved_at ? row("Approved on:", fmt.long(r.approved_at)) : ""}
           ${row("Borrow date:", fmt.long(r.borrow_at))}
           ${row("Return date:", fmt.long(r.return_at))}
           ${r.picked_up_at ? row("Picked up on:", fmt.long(r.picked_up_at)) : ""}
           ${row("Status:", esc(r.status))}
           ${row("Reason for borrowing:", esc(r.purpose))}
         </dl>
         <div class="modal-actions"><button type="button" class="btn btn-outline btn-sm" data-print>Print receipt</button></div>
       </article>`,
      { className: "modal-wide", label: `Receipt ${r.id}` }
    );
    modal.body.querySelector("[data-print]").addEventListener("click", () => window.print());
  }

  R.ItemUI = { openItem, openRequestForm, openSubmitted, openRequestDetails, openBorrowing, openRecord, openReceipt, cancelRequest };
})();
