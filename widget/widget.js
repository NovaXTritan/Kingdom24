/**
 * Kingdom Foods chat widget — single-file loader.
 *
 * Embed on any page with one line:
 *   <script src="https://chatbot.kingdom24.in/widget.js" defer></script>
 *
 * Optional config:
 *   data-api-url     | window.K24_API_URL      — default: same-origin /api/chat
 *   data-greet-delay | window.K24_GREET_DELAY  — ms before tooltip nudge (default 8000)
 *
 * Hardening:
 *   - WCAG 2.1 AA: ARIA labels, keyboard nav, focus management, Esc to close
 *   - Network failure: 1 auto-retry, then WhatsApp fallback
 *   - Offline detection via navigator.onLine
 *   - Send debounce 500ms (no double-send)
 *   - All user input is plain-text only (no innerHTML on user content)
 */
(function () {
  'use strict';

  // ─── Resolve config ────────────────────────────────────────
  function resolveScriptEl() {
    if (document.currentScript) return document.currentScript;
    var scripts = document.getElementsByTagName('script');
    for (var i = scripts.length - 1; i >= 0; i--) {
      var s = scripts[i];
      if ((s.src || '').indexOf('widget.js') !== -1) return s;
    }
    return null;
  }
  var scriptEl = resolveScriptEl();
  var apiBase = (scriptEl && scriptEl.dataset && scriptEl.dataset.apiUrl)
    || window.K24_API_URL
    || (function () {
        if (scriptEl && scriptEl.src) {
          var u = new URL(scriptEl.src, location.href);
          return u.origin + '/api/chat';
        }
        return '/api/chat';
      })();

  // ─── State ────────────────────────────────────────────────
  var STATE = {
    convId: localStorage.getItem('kf_conv_id') || ('cv_' + Math.random().toString(36).slice(2, 14)),
    greeted: false,
    open: false,
    sending: false,           // debounce flag
    online: navigator.onLine,
    lastFocusBeforeOpen: null,
  };
  localStorage.setItem('kf_conv_id', STATE.convId);

  // ─── Render or reuse markup ────────────────────────────────
  var triggerEl = document.getElementById('kf-trigger');
  var panelEl = document.getElementById('kf-panel');
  if (!triggerEl || !panelEl) {
    injectMarkup();
    triggerEl = document.getElementById('kf-trigger');
    panelEl = document.getElementById('kf-panel');
  }

  var messagesEl = document.getElementById('kf-messages');
  var formEl = document.getElementById('kf-form');
  var textEl = document.getElementById('kf-text');
  var sendEl = document.getElementById('kf-send');
  var closeEl = document.getElementById('kf-close');

  // ─── Accessibility setup ──────────────────────────────────
  triggerEl.setAttribute('aria-label', 'Open Kingdom Foods chat assistant');
  triggerEl.setAttribute('type', 'button');
  panelEl.setAttribute('role', 'dialog');
  panelEl.setAttribute('aria-modal', 'false');
  panelEl.setAttribute('aria-label', 'Chat with Kingdom Foods');
  if (messagesEl) {
    messagesEl.setAttribute('role', 'log');
    messagesEl.setAttribute('aria-live', 'polite');
    messagesEl.setAttribute('aria-atomic', 'false');
  }
  if (textEl) textEl.setAttribute('aria-label', 'Type your message');
  if (sendEl) sendEl.setAttribute('aria-label', 'Send message');
  if (closeEl) closeEl.setAttribute('aria-label', 'Close chat');

  // ─── UI events ────────────────────────────────────────────
  triggerEl.addEventListener('click', openPanel);
  closeEl.addEventListener('click', closePanel);
  document.addEventListener('keydown', function (e) {
    if (STATE.open && e.key === 'Escape') {
      e.preventDefault();
      closePanel();
    }
  });
  textEl.addEventListener('input', function () {
    sendEl.disabled = STATE.sending || !textEl.value.trim();
    textEl.style.height = '0px';
    textEl.style.height = Math.min(textEl.scrollHeight, 96) + 'px';
  });
  textEl.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  });
  formEl.addEventListener('submit', function (e) {
    e.preventDefault();
    submit();
  });

  // External trigger
  window.addEventListener('kf:openChat', function (e) {
    openPanel();
    if (e && e.detail && e.detail.message) {
      textEl.value = e.detail.message;
      sendEl.disabled = STATE.sending;
      setTimeout(function () { textEl.focus(); }, 200);
    }
  });

  // Online/offline
  window.addEventListener('online', function () { STATE.online = true; setOnlineBanner(false); enableInput(); });
  window.addEventListener('offline', function () { STATE.online = false; setOnlineBanner(true); disableInputForOffline(); });

  function openPanel() {
    STATE.lastFocusBeforeOpen = document.activeElement;
    panelEl.classList.remove('kf-hidden');
    triggerEl.classList.add('kf-hidden');
    STATE.open = true;
    if (!STATE.greeted) {
      STATE.greeted = true;
      sendInitialGreeting();
    }
    setTimeout(function () { textEl && textEl.focus(); }, 200);
  }
  function closePanel() {
    panelEl.classList.add('kf-hidden');
    triggerEl.classList.remove('kf-hidden');
    STATE.open = false;
    if (STATE.lastFocusBeforeOpen && STATE.lastFocusBeforeOpen.focus) {
      STATE.lastFocusBeforeOpen.focus();
    } else {
      triggerEl.focus();
    }
  }

  function disableInputForOffline() {
    if (textEl) { textEl.disabled = true; textEl.placeholder = 'You are offline'; }
    if (sendEl) sendEl.disabled = true;
  }
  function enableInput() {
    if (textEl) { textEl.disabled = false; textEl.placeholder = 'Type your message…'; }
    if (sendEl) sendEl.disabled = !textEl || !textEl.value.trim();
  }

  function setOnlineBanner(show) {
    var existing = document.getElementById('kf-offline-banner');
    if (show && !existing) {
      var b = document.createElement('div');
      b.id = 'kf-offline-banner';
      b.setAttribute('role', 'status');
      b.style.cssText = 'background:#a83232;color:#fff;padding:6px 12px;font-size:12px;text-align:center';
      b.textContent = 'You are offline. Reconnecting…';
      panelEl.insertBefore(b, panelEl.firstChild.nextSibling);
    } else if (!show && existing) {
      existing.remove();
    }
  }

  function submit() {
    if (STATE.sending) return;
    if (!STATE.online) {
      appendBot(
        "You're offline. Reconnect or WhatsApp +91 8800804580.",
        [], null,
        [{ label: 'Open WhatsApp', message: 'OPEN_WHATSAPP' }]
      );
      return;
    }
    var msg = (textEl.value || '').trim();
    if (!msg) return;

    if (msg === 'OPEN_WHATSAPP') {
      window.open('https://wa.me/918800804580?text=' + encodeURIComponent('Hi Kingdom Foods, I was chatting on the website.'), '_blank', 'noopener,noreferrer');
      textEl.value = '';
      return;
    }
    if (msg === 'OPEN_EMAIL') {
      location.href = 'mailto:sales@kingdom24.in';
      textEl.value = '';
      return;
    }

    appendMessage('user', msg);
    textEl.value = '';
    textEl.style.height = '';
    STATE.sending = true;
    sendEl.disabled = true;
    askApi(msg, /*attempt*/ 1);
    setTimeout(function () { STATE.sending = false; sendEl.disabled = !textEl.value.trim(); }, 500);
  }

  function sendInitialGreeting() {
    askApi('__greeting__', /*attempt*/ 1, /*silent*/ true);
  }

  function askApi(message, attempt, silent) {
    var typingEl = appendTyping();
    var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var timeoutId = setTimeout(function () { try { controller && controller.abort(); } catch (_) {} }, 10000);

    fetch(apiBase, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: STATE.convId,
        message: message,
        metadata: {
          page_url: location.pathname,
          referrer: document.referrer,
          timestamp: new Date().toISOString(),
        },
      }),
      signal: controller ? controller.signal : undefined,
    })
    .then(function (r) {
      clearTimeout(timeoutId);
      if (r.status === 429) {
        return r.json().then(function (j) { throw new RateError(j); });
      }
      return r.json();
    })
    .then(function (data) {
      typingEl.remove();
      if (data && data.conversation_id) {
        STATE.convId = data.conversation_id;
        localStorage.setItem('kf_conv_id', STATE.convId);
      }
      var text = (data && data.reply) || 'Sorry — please try again.';
      appendBot(text, data && data.products, data && data.payment, data && data.actions);
    })
    .catch(function (err) {
      clearTimeout(timeoutId);
      typingEl.remove();
      if (err && err.isRateError) {
        appendBot(
          err.message || "Bahut saare messages aa rahe hain. Thoda ruk kar try karo ya seedha call karo: 8800804580",
          [], null,
          [{ label: 'Open WhatsApp', message: 'OPEN_WHATSAPP' }]
        );
        return;
      }
      // First failure: auto-retry once
      if (attempt < 2 && !silent) {
        appendBot('Connection issue. Retry kar rahe hain…', [], null, []);
        setTimeout(function () { askApi(message, attempt + 1, silent); }, 1500);
        return;
      }
      // Final failure
      appendBot(
        "I'm offline for a moment. Please WhatsApp +91 8800804580 — our team replies within an hour during business hours.",
        [], null,
        [
          { label: 'Open WhatsApp', message: 'OPEN_WHATSAPP' },
          { label: 'Try again', message: message },
        ]
      );
    });
  }

  function RateError(payload) {
    this.isRateError = true;
    this.message = (payload && payload.message) || (payload && payload.error) || '';
  }

  // ─── Renderers (text-only — no innerHTML on user content) ──
  function appendMessage(role, text) {
    var el = document.createElement('div');
    el.className = role === 'user' ? 'kf-msg-user' : 'kf-msg-bot';
    el.setAttribute('role', role === 'user' ? 'note' : 'status');
    el.textContent = text;
    messagesEl.appendChild(el);
    scrollDown();
  }

  function appendBot(text, products, payment, actions) {
    var row = document.createElement('div');
    row.className = 'kf-bot-row';

    var avatar = document.createElement('div');
    avatar.className = 'kf-bot-avatar';
    avatar.setAttribute('aria-hidden', 'true');
    avatar.textContent = 'K';
    row.appendChild(avatar);

    var stack = document.createElement('div');
    stack.className = 'kf-bot-stack';

    var bubble = document.createElement('div');
    bubble.className = 'kf-msg-bot';
    bubble.setAttribute('role', 'note');
    bubble.textContent = text;
    stack.appendChild(bubble);

    if (products && products.length) stack.appendChild(buildProducts(products));
    if (payment) stack.appendChild(buildPayment(payment));
    if (actions && actions.length) stack.appendChild(buildActions(actions));

    row.appendChild(stack);
    messagesEl.appendChild(row);
    scrollDown();
  }

  function buildProducts(products) {
    var row = document.createElement('div');
    row.className = 'kf-products';
    products.forEach(function (p) {
      var card = document.createElement('a');
      card.className = 'kf-product';
      card.href = ('https://kingdom24.in/products/' + encodeURIComponent(p.slug));
      card.target = '_blank';
      card.rel = 'noopener';
      var price = '₹' + Math.round(p.price).toLocaleString('en-IN');
      card.setAttribute('aria-label', p.name + ', ' + price + ', ' + (p.moq || ''));

      var img = document.createElement('div');
      img.className = 'kf-product-img';
      img.setAttribute('aria-hidden', 'true');
      if (p.image) img.style.backgroundImage = 'url("' + p.image + '")';
      card.appendChild(img);

      var body = document.createElement('div');
      body.className = 'kf-product-body';

      var cat = document.createElement('div');
      cat.className = 'kf-product-cat';
      cat.textContent = p.category;
      body.appendChild(cat);

      var name = document.createElement('div');
      name.className = 'kf-product-name';
      name.textContent = p.name;
      body.appendChild(name);

      var meta = document.createElement('div');
      var priceEl = document.createElement('span');
      priceEl.className = 'kf-product-price';
      priceEl.textContent = price + '/kg';
      meta.appendChild(priceEl);
      var moq = document.createElement('span');
      moq.className = 'kf-product-moq';
      moq.textContent = p.moq || '';
      meta.appendChild(moq);
      body.appendChild(meta);

      card.appendChild(body);
      row.appendChild(card);
    });
    return row;
  }

  function buildPayment(p) {
    var wrap = document.createElement('div');
    wrap.className = 'kf-payment';

    var bar = document.createElement('div');
    bar.className = 'kf-payment-bar';
    bar.setAttribute('aria-hidden', 'true');
    wrap.appendChild(bar);

    var body = document.createElement('div');
    body.className = 'kf-payment-body';

    var label = document.createElement('div');
    label.className = 'kf-payment-label';
    label.textContent = 'Secure payment link';
    body.appendChild(label);

    var amt = document.createElement('div');
    amt.className = 'kf-payment-amount';
    amt.textContent = '₹' + Number(p.amount).toLocaleString('en-IN');
    body.appendChild(amt);

    var desc = document.createElement('div');
    desc.className = 'kf-payment-desc';
    desc.textContent = p.description || '';
    body.appendChild(desc);

    var btn = document.createElement('a');
    btn.className = 'kf-payment-btn';
    btn.href = p.url;
    btn.target = '_blank';
    btn.rel = 'noopener noreferrer';
    btn.textContent = 'Pay via Razorpay';
    btn.setAttribute('aria-label', 'Pay ₹' + Number(p.amount).toLocaleString('en-IN') + ' via Razorpay (opens in a new tab)');
    body.appendChild(btn);

    var meta = document.createElement('div');
    meta.className = 'kf-payment-meta';
    meta.textContent = 'UPI · Cards · Net Banking · Link expires in ' + (p.expires_in_minutes || 30) + ' min';
    body.appendChild(meta);

    wrap.appendChild(body);
    return wrap;
  }

  function buildActions(actions) {
    var wrap = document.createElement('div');
    wrap.className = 'kf-actions';
    wrap.setAttribute('role', 'group');
    wrap.setAttribute('aria-label', 'Suggested actions');
    actions.forEach(function (a) {
      var btn = document.createElement('button');
      btn.className = 'kf-action';
      btn.type = 'button';
      btn.textContent = a.label;
      btn.addEventListener('click', function () {
        textEl.value = a.message;
        sendEl.disabled = !a.message;
        submit();
      });
      wrap.appendChild(btn);
    });
    return wrap;
  }

  function appendTyping() {
    var row = document.createElement('div');
    row.className = 'kf-bot-row';
    row.setAttribute('aria-label', 'Assistant is typing');
    var avatar = document.createElement('div');
    avatar.className = 'kf-bot-avatar';
    avatar.setAttribute('aria-hidden', 'true');
    avatar.textContent = 'K';
    row.appendChild(avatar);
    var stack = document.createElement('div');
    stack.className = 'kf-bot-stack';
    var typing = document.createElement('div');
    typing.className = 'kf-typing';
    typing.setAttribute('aria-hidden', 'true');
    typing.innerHTML = '<span></span><span></span><span></span>';
    stack.appendChild(typing);
    row.appendChild(stack);
    messagesEl.appendChild(row);
    scrollDown();
    return row;
  }

  function scrollDown() {
    requestAnimationFrame(function () {
      messagesEl.scrollTo({ top: messagesEl.scrollHeight, behavior: 'smooth' });
    });
  }

  // ─── If embedded standalone, inject the markup + styles ────
  function injectMarkup() {
    var STYLE_URL = (function () {
      if (scriptEl && scriptEl.src) {
        return new URL('chatbot-widget.html', scriptEl.src).href;
      }
      return null;
    })();
    if (STYLE_URL) {
      fetch(STYLE_URL).then(function (r) { return r.text(); }).then(function (html) {
        var match = html.match(/<style[^>]*>([\s\S]*?)<\/style>/);
        if (match) {
          var s = document.createElement('style');
          s.textContent = match[1];
          document.head.appendChild(s);
        }
      }).catch(function () { /* graceful: widget will render unstyled */ });
    }

    var trigger = document.createElement('button');
    trigger.id = 'kf-trigger';
    trigger.className = 'kf-trigger';
    trigger.type = 'button';
    trigger.innerHTML = '<span class="kf-mono" aria-hidden="true">K</span><span>Sales Assistant</span>';
    document.body.appendChild(trigger);

    var panel = document.createElement('section');
    panel.id = 'kf-panel';
    panel.className = 'kf-panel kf-hidden';
    panel.innerHTML = [
      '<header class="kf-header">',
      '  <div class="kf-avatar" aria-hidden="true">K</div>',
      '  <div class="kf-meta"><div class="kf-name">Kingdom Foods</div>',
      '    <div class="kf-status"><span class="kf-dot" aria-hidden="true"></span> AI sales assistant · Online</div></div>',
      '  <button id="kf-close" class="kf-close" type="button">',
      '    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
      '  </button>',
      '</header>',
      '<div id="kf-messages" class="kf-messages"></div>',
      '<div class="kf-input">',
      '  <form id="kf-form">',
      '    <textarea id="kf-text" rows="1" placeholder="Type your message…"></textarea>',
      '    <button id="kf-send" class="kf-send" type="submit" disabled>',
      '      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 12h16M14 6l6 6-6 6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
      '    </button>',
      '  </form>',
      '  <div class="kf-credit">Powered by Kingdom Foods AI · Hindi &amp; English</div>',
      '</div>',
    ].join('\n');
    document.body.appendChild(panel);
  }
})();
