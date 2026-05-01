/**
 * Kingdom Foods chat widget — single-file loader.
 *
 * Embed on any page with one line:
 *   <script src="https://chatbot.kingdom24.in/widget.js" defer></script>
 *
 * Optional config (via the script tag's data-* or window globals BEFORE load):
 *   data-api-url     | window.K24_API_URL    — default: same-origin /api/chat
 *   data-greet-delay | window.K24_GREET_DELAY — ms before tooltip nudge (default 8000)
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
  };
  localStorage.setItem('kf_conv_id', STATE.convId);

  // ─── Render preview HTML if running standalone ─────────────
  // (Production embed appends the markup itself.)
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

  // ─── UI events ────────────────────────────────────────────
  triggerEl.addEventListener('click', openPanel);
  closeEl.addEventListener('click', closePanel);
  textEl.addEventListener('input', function () {
    sendEl.disabled = !textEl.value.trim();
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

  // External trigger: window.dispatchEvent(new CustomEvent('kf:openChat', {detail:{message:'…'}}))
  window.addEventListener('kf:openChat', function (e) {
    openPanel();
    if (e && e.detail && e.detail.message) {
      textEl.value = e.detail.message;
      sendEl.disabled = false;
      setTimeout(function () { textEl.focus(); }, 200);
    }
  });

  function openPanel() {
    panelEl.classList.remove('kf-hidden');
    triggerEl.classList.add('kf-hidden');
    STATE.open = true;
    if (!STATE.greeted) {
      STATE.greeted = true;
      sendInitialGreeting();
    }
    setTimeout(function () { textEl.focus(); }, 200);
  }
  function closePanel() {
    panelEl.classList.add('kf-hidden');
    triggerEl.classList.remove('kf-hidden');
    STATE.open = false;
  }

  function submit() {
    var msg = (textEl.value || '').trim();
    if (!msg) return;

    if (msg === 'OPEN_WHATSAPP') {
      window.open('https://wa.me/918800804580?text=' + encodeURIComponent('Hi Kingdom Foods, I was chatting on the website.'), '_blank', 'noopener,noreferrer');
      textEl.value = '';
      sendEl.disabled = true;
      return;
    }
    if (msg === 'OPEN_EMAIL') {
      location.href = 'mailto:sales@kingdom24.in';
      textEl.value = '';
      sendEl.disabled = true;
      return;
    }

    appendMessage('user', msg);
    textEl.value = '';
    textEl.style.height = '';
    sendEl.disabled = true;
    askApi(msg);
  }

  function sendInitialGreeting() {
    askApi('__greeting__', { silent: true });
  }

  function askApi(message, opts) {
    opts = opts || {};
    var typingEl = appendTyping();
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
    })
    .then(function (r) { return r.json(); })
    .then(function (data) {
      typingEl.remove();
      if (data && data.conversation_id) {
        STATE.convId = data.conversation_id;
        localStorage.setItem('kf_conv_id', STATE.convId);
      }
      var text = (data && data.reply) || 'Sorry — please try again.';
      appendBot(text, data && data.products, data && data.payment, data && data.actions);
    })
    .catch(function () {
      typingEl.remove();
      appendBot(
        "I'm offline for a moment. Please WhatsApp +91 8800804580 or email sales@kingdom24.in — our team replies within an hour during business hours.",
        [], null,
        [{ label: 'Open WhatsApp', message: 'OPEN_WHATSAPP' }]
      );
    });
  }

  // ─── Renderers ────────────────────────────────────────────
  function appendMessage(role, text) {
    var el = document.createElement('div');
    el.className = role === 'user' ? 'kf-msg-user' : 'kf-msg-bot';
    el.textContent = text;
    messagesEl.appendChild(el);
    scrollDown();
  }

  function appendBot(text, products, payment, actions) {
    var row = document.createElement('div');
    row.className = 'kf-bot-row';

    var avatar = document.createElement('div');
    avatar.className = 'kf-bot-avatar';
    avatar.textContent = 'K';
    row.appendChild(avatar);

    var stack = document.createElement('div');
    stack.className = 'kf-bot-stack';

    var bubble = document.createElement('div');
    bubble.className = 'kf-msg-bot';
    bubble.textContent = text;
    stack.appendChild(bubble);

    if (products && products.length) {
      stack.appendChild(buildProducts(products));
    }
    if (payment) {
      stack.appendChild(buildPayment(payment));
    }
    if (actions && actions.length) {
      stack.appendChild(buildActions(actions));
    }

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
      card.href = ('https://kingdom24.in/products/' + p.slug);
      card.target = '_blank';
      card.rel = 'noopener';

      var img = document.createElement('div');
      img.className = 'kf-product-img';
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
      var price = document.createElement('span');
      price.className = 'kf-product-price';
      price.textContent = '₹' + Math.round(p.price).toLocaleString('en-IN') + '/kg';
      meta.appendChild(price);
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
    var avatar = document.createElement('div');
    avatar.className = 'kf-bot-avatar';
    avatar.textContent = 'K';
    row.appendChild(avatar);
    var stack = document.createElement('div');
    stack.className = 'kf-bot-stack';
    var typing = document.createElement('div');
    typing.className = 'kf-typing';
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
    // The styles live in chatbot-widget.html so when embedding via this script
    // alone (e.g. from a CDN), inject a minimal loader that fetches the styles.
    var STYLE_URL = (function () {
      if (scriptEl && scriptEl.src) {
        return new URL('chatbot-widget.html', scriptEl.src).href;
      }
      return null;
    })();
    if (STYLE_URL) {
      // Fetch the HTML file once, extract <style>, append to <head>
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
    trigger.setAttribute('aria-label', 'Chat with Kingdom Foods sales');
    trigger.innerHTML = '<span class="kf-mono">K</span><span>Sales Assistant</span>';
    document.body.appendChild(trigger);

    var panel = document.createElement('section');
    panel.id = 'kf-panel';
    panel.className = 'kf-panel kf-hidden';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Kingdom Foods chat');
    panel.innerHTML = [
      '<header class="kf-header">',
      '  <div class="kf-avatar">K</div>',
      '  <div class="kf-meta"><div class="kf-name">Kingdom Foods</div>',
      '    <div class="kf-status"><span class="kf-dot"></span> AI sales assistant · Online</div></div>',
      '  <button id="kf-close" class="kf-close" type="button" aria-label="Close chat">',
      '    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
      '  </button>',
      '</header>',
      '<div id="kf-messages" class="kf-messages"></div>',
      '<div class="kf-input">',
      '  <form id="kf-form">',
      '    <textarea id="kf-text" rows="1" placeholder="Type your message…" aria-label="Type your message"></textarea>',
      '    <button id="kf-send" class="kf-send" type="submit" disabled aria-label="Send">',
      '      <svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M4 12h16M14 6l6 6-6 6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
      '    </button>',
      '  </form>',
      '  <div class="kf-credit">Powered by Kingdom Foods AI · Hindi &amp; English</div>',
      '</div>',
    ].join('\n');
    document.body.appendChild(panel);
  }
})();
