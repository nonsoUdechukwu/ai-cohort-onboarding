(function () {
  "use strict";

  var form = document.getElementById("register-form");
  if (!form) return;

  var statusBox = document.getElementById("status");
  var button = document.getElementById("submit");
  var spinner = button.querySelector(".spinner");
  var label = button.querySelector(".label");
  var emailRe = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  function setFieldError(input, errorId, show) {
    var err = document.getElementById(errorId);
    input.setAttribute("aria-invalid", show ? "true" : "false");
    if (err) err.hidden = !show;
  }

  function showStatus(kind, nodes) {
    statusBox.className = "status status-" + kind;
    statusBox.textContent = "";
    nodes.forEach(function (n) { statusBox.appendChild(n); });
    statusBox.hidden = false;
    statusBox.focus();
  }

  function para(text) {
    var p = document.createElement("p");
    p.textContent = text;
    return p;
  }

  function setBusy(busy) {
    button.disabled = busy;
    spinner.hidden = !busy;
    label.textContent = busy ? "Sending invitation…" : "Send my invitation";
  }

  function resetCaptcha() {
    if (window.turnstile && typeof window.turnstile.reset === "function") {
      try { window.turnstile.reset(); } catch (e) { /* ignore */ }
    }
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var email = form.email;
    var code = form.access_code;
    var emailOk = emailRe.test(email.value.trim());
    var codeOk = code.value.trim().length > 0;
    setFieldError(email, "email-error", !emailOk);
    setFieldError(code, "code-error", !codeOk);
    if (!emailOk) { email.focus(); return; }
    if (!codeOk) { code.focus(); return; }

    var tokenInput = form.querySelector('[name="cf-turnstile-response"]');
    if (!tokenInput || !tokenInput.value) {
      showStatus("error", [para("Please complete the human verification check first.")]);
      return;
    }

    setBusy(true);
    fetch(form.action, {
      method: "POST",
      body: new FormData(form),
      headers: { "Accept": "application/json" },
      credentials: "same-origin"
    })
      .then(function (resp) {
        return resp.json().catch(function () { return { ok: false }; });
      })
      .then(function (data) {
        if (data && data.ok) {
          var nodes = [para(data.message)];
          if (data.redeemUrl && /^https:\/\//i.test(data.redeemUrl)) {
            var p = document.createElement("p");
            p.appendChild(document.createTextNode("Didn't get the email? "));
            var a = document.createElement("a");
            a.href = data.redeemUrl;
            a.rel = "noopener noreferrer";
            a.textContent = "Accept your invitation directly";
            p.appendChild(a);
            nodes.push(p);
          }
          showStatus("success", nodes);
          form.reset();
        } else {
          showStatus("error", [para((data && data.error) || "Something went wrong. Please try again.")]);
        }
      })
      .catch(function () {
        showStatus("error", [para("Network error. Please check your connection and try again.")]);
      })
      .then(function () {
        setBusy(false);
        resetCaptcha();
      });
  });
})();
