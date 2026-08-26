/* Patient Portal — frontend.
 *
 * Identity is a cookie, not a session. Switching users is a client-side
 * change; there is no server call for it. In production the cookie would be
 * a signed session or a JWT and the switcher would not exist, but every
 * authorization check on the server stays exactly where it is.
 *
 * Note what this file does NOT do: it never decides whether an action is
 * allowed. It hides buttons the current user cannot use, which is a
 * convenience. The server refuses the request regardless.
 */
 
const api = {
  async call(method, path, body) {
    const res = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    const data = text ? JSON.parse(text) : null;
    if (!res.ok) throw { status: res.status, data };
    return data;
  },
  get: (p) => api.call("GET", p),
  post: (p, b) => api.call("POST", p, b),
  patch: (p, b) => api.call("PATCH", p, b),
};
 
/* ------------------------------------------------------------- identity */
 
const identity = {
  get() {
    const m = document.cookie.match(/(?:^|;\s*)user_id=(\d+)/);
    return m ? Number(m[1]) : null;
  },
  set(id) {
    // Survives a refresh, which is the "persistent enough" requirement
    // applied to the switcher itself and not just the data.
    document.cookie = `user_id=${id}; path=/; max-age=31536000; SameSite=Lax`;
  },
};
 
let currentUser = null;
let allUsers = [];
 
/* ------------------------------------------------------------ formatting
 * History is stored structured, so every timestamp is formatted here at
 * display time rather than frozen into the stored record. */
 
const fmtDate = new Intl.DateTimeFormat(undefined, {
  weekday: "short", day: "numeric", month: "short", year: "numeric",
});
const fmtTime = new Intl.DateTimeFormat(undefined, {
  hour: "2-digit", minute: "2-digit",
});
 
const dateOf = (iso) => fmtDate.format(new Date(iso));
const timeOf = (iso) => fmtTime.format(new Date(iso));
 
function span(startIso, endIso) {
  return `${timeOf(startIso)} – ${timeOf(endIso)}`;
}
 
function relative(iso) {
  const then = new Date(iso);
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  if (mins < 1440) return `${Math.round(mins / 60)} h ago`;
  return `${dateOf(iso)}, ${timeOf(iso)}`;
}
 
const TYPE_LABEL = {
  consultation: "Consultation",
  follow_up: "Follow-up",
  annual_physical: "Annual physical",
  lab_review: "Lab review",
};
 
const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
 
function pill(status) {
  return `<span class="pill pill-${status}">${status}</span>`;
}
 
function toast(message) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 3200);
}
 
function messageFrom(err) {
  const d = err?.data?.detail;
  if (typeof d === "string") return d;
  if (d?.message) return d.message;
  return "Something went wrong. Try again.";
}
 
/* ------------------------------------------------------------- switcher */
 
async function initSwitcher() {
  allUsers = await api.get("/api/users");
  if (!allUsers.length) return;
 
  let id = identity.get();
  if (!allUsers.some((u) => u.id === id)) {
    id = allUsers[0].id;
    identity.set(id);
  }
  currentUser = allUsers.find((u) => u.id === id);
 
  const select = document.getElementById("user-select");
  select.innerHTML = allUsers
    .map((u) => `<option value="${u.id}">${esc(u.name)} — ${u.role}</option>`)
    .join("");
  select.value = String(id);
 
  select.addEventListener("change", () => {
    identity.set(Number(select.value));
    // Reload rather than patching state in place: the whole page depends on
    // who you are, so re-rendering from scratch is both simpler and safer.
    window.location.reload();
  });
}
 
/* ----------------------------------------------------------------- list */
 
function rowHtml(a) {
  const other = currentUser.role === "patient" ? a.provider_name : a.patient_name;
  const label = currentUser.role === "patient" ? "with" : "for";
  return `
    <a class="row ${a.status === "cancelled" ? "is-cancelled" : ""}"
       href="/appointments/${a.id}">
      <div class="row-when">
        <time datetime="${a.starts_at}">${dateOf(a.starts_at)}</time>
        <small><time datetime="${a.starts_at}">${span(a.starts_at, a.ends_at)}</time></small>
      </div>
      <div class="row-who">
        <strong>${esc(label)} ${esc(other)}</strong>
        <span>${esc(TYPE_LABEL[a.appointment_type])}${
          a.reason ? " · " + esc(a.reason) : ""}</span>
      </div>
      ${pill(a.status)}
    </a>`;
}
 
async function renderList() {
  const list = document.getElementById("list");
  const isPatient = currentUser.role === "patient";
 
  document.getElementById("page-title").textContent = isPatient
    ? "Your appointments"
    : "Your schedule";
 
  document.getElementById("scope-note").textContent = isPatient
    ? "Requests you have made and appointments booked for you."
    : "Every appointment assigned to you, including requests waiting on a decision.";
 
  const requestBtn = document.getElementById("request-btn");
  requestBtn.hidden = !isPatient;
 
  const appointments = await api.get("/api/appointments");
  list.setAttribute("aria-busy", "false");
 
  list.innerHTML = appointments.length
    ? appointments.map(rowHtml).join("")
    : `<p class="empty">Nothing scheduled yet.${
        isPatient ? " Request an appointment to get started." : ""}</p>`;
}
 
/* --------------------------------------------------------- request form */

// Bookable hours: 9:00 AM to 5:00 PM, in 15-minute slots.
function timeSlotOptions() {
  const opts = [];
  for (let minutes = 9 * 60; minutes < 17 * 60; minutes += 15) {
    const h24 = Math.floor(minutes / 60);
    const m = minutes % 60;
    const value = `${String(h24).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
    const label = new Date(2000, 0, 1, h24, m).toLocaleTimeString([], {
      hour: "numeric",
      minute: "2-digit",
    });
    opts.push(`<option value="${value}">${label}</option>`);
  }
  return opts.join("");
}

async function initRequestForm() {
  const dialog = document.getElementById("request-dialog");
  if (!dialog) return;

  const providers = await api.get("/api/providers");
  document.getElementById("f-provider").innerHTML = providers
    .map((p) => `<option value="${p.id}">${esc(p.name)}</option>`)
    .join("");

  document.getElementById("f-time").innerHTML = timeSlotOptions();
  // Can't request a slot before today.
  document.getElementById("f-date").min = new Date().toISOString().slice(0, 10);

  document.getElementById("request-btn").addEventListener("click", () => {
    document.getElementById("request-error").hidden = true;
    dialog.showModal();
  });
  document.getElementById("request-cancel").addEventListener("click", () =>
    dialog.close());
 
  document.getElementById("request-submit").addEventListener("click", async () => {
    const date = document.getElementById("f-date").value;
    const time = document.getElementById("f-time").value;
    const errEl = document.getElementById("request-error");

    if (!date || !time) {
      errEl.textContent = "Pick a date and time.";
      errEl.hidden = false;
      return;
    }

    try {
      await api.post("/api/appointments", {
        provider_id: Number(document.getElementById("f-provider").value),
        appointment_type: document.getElementById("f-type").value,
        // The local date + slot are combined and converted to a UTC
        // instant here. The server derives the end time from the type.
        starts_at: new Date(`${date}T${time}`).toISOString(),
        reason: document.getElementById("f-reason").value || null,
      });
      dialog.close();
      toast("Request sent. It will show as pending until a provider confirms.");
      await renderList();
    } catch (err) {
      errEl.textContent = messageFrom(err);
      errEl.hidden = false;
    }
  });
}
 
/* --------------------------------------------------------------- detail */
 
function historyLine(h) {
  const who = `<b>${esc(h.actor_name)}</b>`;
  if (h.field_changed === "starts_at") {
    if (h.old_value === null) {
      return `${who} requested <time>${dateOf(h.new_value)}, ${
        timeOf(h.new_value)}</time>`;
    }
    return `${who} moved it from <span class="from"><time>${
      dateOf(h.old_value)}, ${timeOf(h.old_value)}</time></span> to <time>${
      dateOf(h.new_value)}, ${timeOf(h.new_value)}</time>`;
  }
  if (h.old_value === null) return `${who} created the request`;
  return `${who} changed status from ${esc(h.old_value)} to <b>${esc(h.new_value)}</b>`;
}
 
function detailHtml(a) {
  const isPatient = currentUser.role === "patient";
  const buttons = [];
 
  // The patient has exactly one verb, and only on a confirmed appointment.
  // There is no patient confirm button: the spec's status flow and patient
  // view give them cancel and nothing else.
  if (isPatient && a.status === "confirmed") {
    buttons.push(`<button class="btn btn-danger" data-action="cancel">
      Cancel appointment</button>`);
  }
  if (!isPatient && a.status === "pending") {
    buttons.push(`<button class="btn btn-primary" data-action="confirm">
      Confirm</button>`);
  }
  if (!isPatient && a.status !== "cancelled") {
    buttons.push(`<button class="btn" data-action="reschedule">Reschedule</button>`);
  }
 
  const trail = a.history
    .map((h, i) => `
      <li class="${i === a.history.length - 1 ? "is-latest" : ""}">
        <span class="trail-when">${relative(h.created_at)}</span>
        <span class="trail-what">${historyLine(h)}</span>
      </li>`)
    .join("");
 
  return `
    <article class="card">
      <div class="detail-head">
        <h1 class="detail-when">
          <time datetime="${a.starts_at}">${dateOf(a.starts_at)}</time>
          <small><time>${span(a.starts_at, a.ends_at)}</time></small>
        </h1>
        ${pill(a.status)}
      </div>
 
      <dl class="facts">
        <dt>Type</dt><dd>${esc(TYPE_LABEL[a.appointment_type])}</dd>
        <dt>Provider</dt><dd>${esc(a.provider_name)}</dd>
        <dt>Patient</dt><dd>${esc(a.patient_name)}</dd>
        <dt>Reason</dt><dd>${esc(a.reason) || "<span class='muted'>Not given</span>"}</dd>
      </dl>
 
      ${buttons.length
        ? `<div class="actions">${buttons.join("")}</div>`
        : `<p class="no-actions">${
            a.status === "cancelled"
              ? "This appointment was cancelled. Nothing further to do."
              : isPatient
                ? "Nothing to do while this request is pending. You can cancel it once it is confirmed."
                : "No actions available."}</p>`}
 
      <div id="conflict-slot"></div>
    </article>
 
    <section class="history">
      <h2>History</h2>
      <p class="history-note">
        Every change is recorded and nothing is overwritten.
      </p>
      <ol class="trail">${trail}</ol>
    </section>`;
}
 
async function renderDetail(id) {
  const host = document.getElementById("detail");
  const a = await api.get(`/api/appointments/${id}`);
  host.setAttribute("aria-busy", "false");
  host.innerHTML = detailHtml(a);
  wireActions(a);
  return a;
}
 
/* ------------------------------------------------- the Problem 1 moment
 * A write carries the version the screen was rendered from. If the row has
 * moved on, the server refuses it. What happens next is the interesting
 * half: the appointment is re-fetched, the new state is shown, and the
 * question is asked again. The patient's intent was probably "I can't make
 * 2:00" — and 4:00 might suit them fine. */
 
async function handleConflict(id, action, err) {
  const fresh = await renderDetail(id);
  const slot = document.getElementById("conflict-slot");
 
  if (err?.data?.detail?.code === "slot_taken") {
    slot.innerHTML = `<div class="conflict"><p>${esc(messageFrom(err))}</p></div>`;
    return;
  }
 
  const verb = action === "cancel" ? "cancel" : "confirm";
  slot.innerHTML = `
    <div class="conflict">
      <p>${esc(messageFrom(err))} Do you still want to ${verb} it?</p>
      <div class="conflict-actions">
        <button class="btn btn-primary" data-action="${action}">
          Yes, ${verb} it</button>
        <button class="btn btn-quiet" data-action="dismiss">Leave it as is</button>
      </div>
    </div>`;
  wireActions(fresh);
}
 
function wireActions(a) {
  document.querySelectorAll("[data-action]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const action = btn.dataset.action;
      if (action === "dismiss") {
        document.getElementById("conflict-slot").innerHTML = "";
        return;
      }
      if (action === "reschedule") {
        openReschedule(a);
        return;
      }
 
      btn.disabled = true;
      try {
        // expected_version is what makes the write conditional. The server
        // turns it into WHERE version = ?, so two racing requests cannot
        // both succeed regardless of what either read a moment earlier.
        await api.post(`/api/appointments/${a.id}/${action}`, {
          expected_version: a.version,
        });
        toast(action === "confirm" ? "Appointment confirmed." : "Appointment cancelled.");
        await renderDetail(a.id);
      } catch (err) {
        if (err.status === 409) await handleConflict(a.id, action, err);
        else toast(messageFrom(err));
      }
    });
  });
}
 
function openReschedule(a) {
  const dialog = document.getElementById("reschedule-dialog");
  const dateInput = document.getElementById("r-date");
  const timeSelect = document.getElementById("r-time");
  const errEl = document.getElementById("reschedule-error");

  timeSelect.innerHTML = timeSlotOptions();
  dateInput.min = new Date().toISOString().slice(0, 10);

  // Prefill from the appointment's current local date/time, snapping the
  // time down to the nearest 15-minute slot the dropdown actually offers.
  const local = new Date(a.starts_at);
  const pad = (n) => String(n).padStart(2, "0");
  dateInput.value = `${local.getFullYear()}-${pad(local.getMonth() + 1)}-${pad(local.getDate())}`;
  const snapped = `${pad(local.getHours())}:${pad(Math.floor(local.getMinutes() / 15) * 15)}`;
  timeSelect.value = timeSelect.querySelector(`option[value="${snapped}"]`) ? snapped : "09:00";

  errEl.hidden = true;
  dialog.showModal();

  document.getElementById("reschedule-cancel").onclick = () => dialog.close();
  document.getElementById("reschedule-submit").onclick = async () => {
    try {
      await api.patch(`/api/appointments/${a.id}/reschedule`, {
        starts_at: new Date(`${dateInput.value}T${timeSelect.value}`).toISOString(),
        expected_version: a.version,
      });
      dialog.close();
      toast("Appointment moved. Status is unchanged.");
      await renderDetail(a.id);
    } catch (err) {
      if (err.status === 409 && err.data?.detail?.code === "stale_version") {
        dialog.close();
        await handleConflict(a.id, "reschedule", err);
        return;
      }
      errEl.textContent = messageFrom(err);
      errEl.hidden = false;
    }
  };
}
 
/* ------------------------------------------------------------------ boot */
 
(async function boot() {
  try {
    await initSwitcher();
    if (!currentUser) return;
 
    const page = document.querySelector("[data-appointment-id]");
    if (page) {
      await renderDetail(Number(page.dataset.appointmentId));
    } else {
      await renderList();
      await initRequestForm();
    }
  } catch (err) {
    if (err.status === 403) {
      document.getElementById("main").innerHTML =
        `<div class="page page-narrow"><p class="empty">
          That appointment belongs to someone else.
          <a href="/">Back to your appointments</a>.
        </p></div>`;
      return;
    }
    console.error(err);
    toast(messageFrom(err));
  }
})();