/**
 * Kingdom Foods AI Sales Assistant — Comet-style agent widget.
 *
 * Single-file, self-contained, no dependencies. Mounts in a Shadow DOM so
 * host-page CSS can't bleed in. Drops onto any page with one line:
 *
 *   <script src="https://chatbot.kingdom24.in/widget.js" defer></script>
 *
 * Optional config:
 *   data-api-url    on the script tag, OR
 *   window.K24_API_URL              — overrides default (same-origin /api/chat)
 *   window.KF_CHATBOT_API           — alias accepted for backward-compat
 *   data-greet-delay / window.K24_GREET_DELAY — ms before first-visit tooltip (default 8000)
 *   data-site-url   / window.KF_SITE_URL    — public storefront origin for product
 *                                              deep-links. EMPTY = stay in chat
 *                                              (clicking a product card sends a
 *                                              "tell me more" message instead of
 *                                              opening a possibly-404 URL).
 *                                              Set to 'https://kingdom24.in' once
 *                                              the public site is live.
 *
 * Programmatic open / preset:
 *   window.dispatchEvent(new CustomEvent('kf:openChat', { detail: { message: '...' } }));
 *
 * Designed for: customer thinks "this company has serious technology" within
 * 5 seconds of opening it. The agent task sequence makes waiting feel like
 * watching the AI work, not buffering.
 */
(function () {
  'use strict';

  if (window.__KF_WIDGET_MOUNTED__) return;
  window.__KF_WIDGET_MOUNTED__ = true;

  // ─────────────────────────────────────────────────────────────────
  // Config
  // ─────────────────────────────────────────────────────────────────
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
  var apiBase =
    (scriptEl && scriptEl.dataset && scriptEl.dataset.apiUrl) ||
    window.K24_API_URL ||
    window.KF_CHATBOT_API ||
    (function () {
      if (scriptEl && scriptEl.src) {
        try {
          return new URL(scriptEl.src, location.href).origin + '/api/chat';
        } catch (_) {}
      }
      return '/api/chat';
    })();
  var greetDelay = parseInt(
    (scriptEl && scriptEl.dataset && scriptEl.dataset.greetDelay) ||
      window.K24_GREET_DELAY ||
      '8000',
    10
  );

  // Public site URL for product deep-links. Empty string ⇒ keep the user
  // inside the chat (clicking a product card sends a "tell me more" message
  // instead of opening an external URL that may 404). Set this once the
  // public site is live: window.KF_SITE_URL = 'https://kingdom24.in'.
  var siteUrl =
    (scriptEl && scriptEl.dataset && scriptEl.dataset.siteUrl) ||
    (window.KF_SITE_URL !== undefined ? window.KF_SITE_URL : '');
  // Trim trailing slash for cleaner concatenation
  if (siteUrl && siteUrl.charAt(siteUrl.length - 1) === '/') {
    siteUrl = siteUrl.slice(0, -1);
  }

  var REDUCE_MOTION =
    window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ─────────────────────────────────────────────────────────────────
  // State
  // ─────────────────────────────────────────────────────────────────
  var STATES = {
    IDLE: 'idle',
    OPENING: 'opening',
    READY: 'ready',
    THINKING: 'thinking',
    RESPONDING: 'responding',
    CLOSING: 'closing',
  };
  var S = {
    state: STATES.IDLE,
    convId:
      localStorage.getItem('kf_conv_id') ||
      'cv_' + Math.random().toString(36).slice(2, 14),
    open: false,
    greeted: false,
    sending: false,
    online: navigator.onLine !== false,
    soundOn: false,
    soundCtx: null,
    lastFocus: null,
    panelEverOpened: false,
    pendingTitleCount: 0,
  };
  localStorage.setItem('kf_conv_id', S.convId);

  // ─────────────────────────────────────────────────────────────────
  // Stylesheet (kept compact; one place to tune the look)
  // ─────────────────────────────────────────────────────────────────
  var STYLES = `
:host { all: initial; }
*, *::before, *::after { box-sizing: border-box; }
.kf-root {
  font-family: 'DM Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  color: #1a1917;
  font-size: 14px;
  line-height: 1.5;
}
.kf-mono { font-family: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace; }

/* ─── Trigger pill (idle) ───────────────────────────────────── */
.kf-trigger {
  position: fixed; right: 24px; bottom: 24px; z-index: 2147483640;
  display: inline-flex; align-items: center; gap: 10px;
  padding: 10px 18px 10px 14px;
  background: linear-gradient(135deg, #1a472a 0%, #0f2a1a 100%);
  color: #fff;
  border: none; border-radius: 999px; cursor: pointer;
  font: 600 13.5px/1 'DM Sans', sans-serif; letter-spacing: 0.01em;
  box-shadow: 0 4px 15px rgba(26,71,42,0.25), 0 0 0 1px rgba(255,255,255,0.04) inset;
  backdrop-filter: blur(8px);
  animation: kf-glow 3s ease-in-out infinite;
  transition: transform .25s cubic-bezier(.32,.72,0,1), box-shadow .25s ease;
  isolation: isolate;
}
.kf-trigger:hover { transform: translateY(-1px) scale(1.02); }
.kf-trigger:focus-visible { outline: 2px solid #b8860b; outline-offset: 3px; }
.kf-trigger.kf-hidden { opacity: 0; pointer-events: none; transform: scale(.85); transition: opacity .2s, transform .2s; }
.kf-trigger::before {
  content: ''; position: absolute; inset: -1.5px; border-radius: 999px; padding: 1.5px;
  background: conic-gradient(from var(--kf-angle, 0deg), transparent 0deg, rgba(232,208,148,.55) 60deg, transparent 120deg, transparent 360deg);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
          mask-composite: exclude;
  pointer-events: none; z-index: -1;
  animation: kf-border-spin 4s linear infinite;
}
@property --kf-angle { syntax: '<angle>'; initial-value: 0deg; inherits: false; }

.kf-trigger-icon {
  position: relative; width: 20px; height: 20px;
  display: inline-flex; align-items: center; justify-content: center;
}
.kf-trigger-icon::before {
  content: '✦'; font-size: 16px; color: #e9d094;
}
.kf-trigger-icon::after {
  content: ''; position: absolute; width: 4px; height: 4px; border-radius: 50%;
  background: #4ade80; box-shadow: 0 0 6px rgba(74,222,128,0.6);
  top: 50%; left: 50%; margin: -2px 0 0 -2px;
  animation: kf-orbit 4s linear infinite;
}

.kf-tip {
  position: fixed; right: 24px; bottom: 80px; z-index: 2147483639;
  background: #fff; color: #1a1917;
  padding: 10px 14px; border-radius: 12px;
  border: 1px solid #f0ede8;
  box-shadow: 0 8px 24px rgba(0,0,0,.12);
  font: 500 13px/1.4 'DM Sans', sans-serif;
  max-width: 240px;
  opacity: 0; transform: translateY(6px);
  transition: opacity .35s ease, transform .35s ease;
  pointer-events: none;
}
.kf-tip.kf-show { opacity: 1; transform: translateY(0); }
.kf-tip::after {
  content: ''; position: absolute; right: 32px; bottom: -6px;
  width: 12px; height: 12px; background: #fff; transform: rotate(45deg);
  border-right: 1px solid #f0ede8; border-bottom: 1px solid #f0ede8;
}

/* ─── Panel ─────────────────────────────────────────────────── */
.kf-panel {
  position: fixed; right: 24px; bottom: 24px; z-index: 2147483641;
  width: 420px; height: 640px;
  display: flex; flex-direction: column;
  background: #faf9f7;
  border: 1px solid rgba(0,0,0,.06);
  border-radius: 24px;
  box-shadow: 0 8px 48px rgba(0,0,0,.12), 0 0 0 1px rgba(0,0,0,.03);
  overflow: hidden;
  transform-origin: bottom right;
  transform: scale(.85) translateY(20px); opacity: 0; pointer-events: none;
  transition: opacity .35s cubic-bezier(.32,.72,0,1), transform .4s cubic-bezier(.32,.72,0,1);
}
.kf-panel.kf-open { transform: scale(1) translateY(0); opacity: 1; pointer-events: auto; }

/* Header */
.kf-header {
  display: flex; align-items: center; gap: 12px;
  padding: 14px 16px;
  background: linear-gradient(135deg, #1a472a 0%, #0f2a1a 100%);
  color: #fff;
  position: relative;
}
.kf-header::after {
  content: ''; position: absolute; left: 0; right: 0; bottom: 0; height: 1px;
  background: linear-gradient(90deg, transparent, rgba(232,208,148,.35), transparent);
}
.kf-avatar {
  width: 36px; height: 36px; border-radius: 50%;
  background: rgba(255,255,255,.08);
  display: inline-flex; align-items: center; justify-content: center;
  position: relative;
  border: 1px solid rgba(232,208,148,.25);
}
.kf-avatar::before { content: '✦'; font-size: 16px; color: #e9d094; }
.kf-avatar::after {
  content: ''; position: absolute; width: 4px; height: 4px; border-radius: 50%;
  background: #4ade80; top: 50%; left: 50%; margin: -2px 0 0 -2px;
  box-shadow: 0 0 6px rgba(74,222,128,.65);
  animation: kf-orbit 3s linear infinite;
}
.kf-meta { flex: 1; min-width: 0; }
.kf-name { font: 600 15px/1.2 'DM Sans', sans-serif; }
.kf-status {
  margin-top: 3px; font-size: 11.5px; font-weight: 500;
  color: rgba(255,255,255,.65); display: flex; align-items: center; gap: 6px;
  letter-spacing: .01em;
}
.kf-status .kf-dot {
  width: 6px; height: 6px; border-radius: 50%; background: #4ade80;
  box-shadow: 0 0 6px rgba(74,222,128,.7);
  animation: kf-pulse-dot 2s ease-in-out infinite;
}
.kf-close {
  width: 28px; height: 28px; border-radius: 999px; cursor: pointer;
  background: rgba(255,255,255,.1); border: none; color: #fff;
  display: inline-flex; align-items: center; justify-content: center;
  transition: background .15s, transform .15s;
}
.kf-close:hover { background: rgba(255,255,255,.2); transform: scale(1.05); }
.kf-close:focus-visible { outline: 2px solid #e9d094; outline-offset: 2px; }

/* Messages */
.kf-messages {
  flex: 1; overflow-y: auto; padding: 16px;
  scroll-behavior: smooth;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: thin; scrollbar-color: rgba(0,0,0,.12) transparent;
}
.kf-messages::-webkit-scrollbar { width: 6px; }
.kf-messages::-webkit-scrollbar-thumb { background: rgba(0,0,0,.12); border-radius: 3px; }
.kf-messages::-webkit-scrollbar-track { background: transparent; }

.kf-row { display: flex; gap: 10px; margin-bottom: 14px; max-width: 100%; }
.kf-row.kf-user { justify-content: flex-end; }
.kf-bubble {
  max-width: 84%; padding: 10px 14px; border-radius: 16px;
  font-size: 14px; line-height: 1.5; word-wrap: break-word; overflow-wrap: anywhere;
  white-space: pre-wrap;
  animation: kf-slide-in .3s cubic-bezier(.32,.72,0,1);
}
.kf-bubble.kf-user-bubble {
  background: #1a472a; color: #fff; border-bottom-right-radius: 4px;
  animation-name: kf-slide-right;
}
.kf-bubble.kf-bot-bubble {
  background: #fff; color: #1a1917; border-bottom-left-radius: 4px;
  border: 1px solid #f0ede8;
  animation-name: kf-slide-left;
}
.kf-bot-avatar {
  width: 28px; height: 28px; border-radius: 50%; flex-shrink: 0;
  background: linear-gradient(135deg, #1a472a, #0f2a1a);
  color: #e9d094; display: inline-flex; align-items: center; justify-content: center;
  font-size: 12px; align-self: flex-end;
}
.kf-bot-avatar::before { content: '✦'; }

/* Task card (the agent experience) */
.kf-task {
  background: #fff; border: 1px solid #e5e2dc; border-radius: 16px;
  padding: 14px 16px; box-shadow: 0 2px 8px rgba(0,0,0,.04);
  max-width: 88%;
  animation: kf-slide-left .3s cubic-bezier(.32,.72,0,1);
}
.kf-task-head {
  display: flex; align-items: center; gap: 8px;
  font: 600 13.5px/1 'DM Sans', sans-serif; color: #1a472a;
  margin-bottom: 12px;
}
.kf-task-spark {
  display: inline-flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; position: relative;
}
.kf-task-spark::before { content: '✦'; font-size: 14px; color: #b8860b; }
.kf-task-spark::after {
  content: ''; position: absolute; width: 3px; height: 3px; border-radius: 50%;
  background: #4ade80; top: 50%; left: 50%; margin: -1.5px 0 0 -1.5px;
  animation: kf-orbit-fast 1.5s linear infinite;
}
.kf-task-steps { display: flex; flex-direction: column; gap: 7px; margin-bottom: 12px; }
.kf-step {
  display: flex; align-items: center; gap: 9px;
  font-size: 12.5px; color: #9c978e;
  transition: color .35s ease, opacity .35s ease;
}
.kf-step-icon {
  width: 14px; height: 14px; flex-shrink: 0;
  display: inline-flex; align-items: center; justify-content: center;
  position: relative;
}
.kf-step-icon::before {
  content: ''; width: 10px; height: 10px; border-radius: 50%;
  border: 1.5px solid #d4cfc5; background: transparent;
  transition: all .35s ease;
}
.kf-step.kf-active { color: #1a472a; font-weight: 500; }
.kf-step.kf-active .kf-step-icon::before {
  border-color: #b8860b; border-top-color: transparent;
  animation: kf-spin 1s linear infinite;
}
.kf-step.kf-done { color: #1a472a; }
.kf-step.kf-done .kf-step-icon::before {
  width: 14px; height: 14px;
  background: #1a472a; border-color: #1a472a;
  animation: kf-check-pop .3s cubic-bezier(.32,.72,.5,1.35);
}
.kf-step.kf-done .kf-step-icon::after {
  content: ''; position: absolute; width: 4px; height: 7px;
  border: solid #fff; border-width: 0 2px 2px 0;
  transform: rotate(45deg) translate(-1px, -1.5px);
  animation: kf-fade-in .25s ease;
}
.kf-step.kf-done.kf-faded { opacity: .55; }
.kf-task-bar {
  height: 3px; background: #efece6; border-radius: 2px; overflow: hidden;
}
.kf-task-bar-fill {
  height: 100%; width: 0%;
  background: linear-gradient(90deg, #1a472a, #2d7a4a);
  border-radius: 2px;
  transition: width .35s ease;
}
.kf-task.kf-collapsing { transition: max-height .3s ease, opacity .3s ease, padding .3s ease, margin .3s ease; max-height: 0; opacity: 0; padding-top: 0; padding-bottom: 0; margin: 0; overflow: hidden; }

/* Typing dots (used for greetings / lightweight intents) */
.kf-typing { display: inline-flex; gap: 4px; padding: 12px 14px; }
.kf-typing span {
  width: 6px; height: 6px; border-radius: 50%; background: #b6b1a7;
  animation: kf-bounce 1.2s ease-in-out infinite;
}
.kf-typing span:nth-child(2) { animation-delay: .15s; }
.kf-typing span:nth-child(3) { animation-delay: .3s; }

/* Action chips */
.kf-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; max-width: 100%; }
.kf-action {
  position: relative; overflow: hidden;
  background: #fff; border: 1px solid #e5e2dc; border-radius: 999px;
  padding: 7px 14px; font: 500 12.5px/1.2 'DM Sans', sans-serif; color: #1a1917;
  cursor: pointer;
  transition: border-color .15s, color .15s, transform .12s;
  animation: kf-fade-up .3s ease both;
}
.kf-action::before {
  content: ''; position: absolute; inset: 0;
  background: #e8f0eb; transform: translateX(-100%); transition: transform .25s ease;
  z-index: -1;
}
.kf-action:hover { border-color: #2d7a4a; color: #1a472a; }
.kf-action:hover::before { transform: translateX(0); }
.kf-action:active { transform: scale(.96); }
.kf-action:focus-visible { outline: 2px solid #b8860b; outline-offset: 2px; }

/* Product cards */
.kf-products { display: flex; flex-direction: column; gap: 8px; margin-top: 10px; max-width: 100%; }
.kf-product {
  display: flex; gap: 12px; padding: 10px 12px;
  background: #fff; border: 1px solid #f0ede8; border-radius: 12px;
  text-decoration: none; color: inherit; cursor: pointer;
  text-align: left; font: inherit; width: 100%;
  transition: transform .2s ease, box-shadow .2s ease, border-color .2s ease;
  animation: kf-card-in .35s cubic-bezier(.32,.72,0,1) both;
}
button.kf-product { /* button-as-card reset */
  -webkit-appearance: none; appearance: none;
}
.kf-product:hover {
  transform: translateY(-2px);
  box-shadow: 0 6px 18px rgba(0,0,0,.06);
  border-color: #d4cfc5;
}
.kf-product:focus-visible { outline: 2px solid #b8860b; outline-offset: 2px; }
.kf-product-cta {
  margin-top: 6px;
  font: 500 11.5px/1 'DM Sans', sans-serif;
  color: #2d7a4a;
  letter-spacing: .01em;
}
.kf-product:hover .kf-product-cta { text-decoration: underline; }
.kf-product-img {
  width: 64px; height: 64px; flex-shrink: 0; border-radius: 8px;
  background: #f5f3ef center/cover no-repeat;
  position: relative; overflow: hidden;
}
.kf-product-img::after {
  content: ''; position: absolute; inset: 0;
  background: linear-gradient(135deg, transparent 60%, rgba(0,0,0,.04));
}
.kf-product-body { flex: 1; min-width: 0; display: flex; flex-direction: column; justify-content: center; }
.kf-product-cat {
  font: 600 9.5px/1 'DM Sans', sans-serif; color: #9c978e;
  text-transform: uppercase; letter-spacing: .14em; margin-bottom: 4px;
}
.kf-product-name {
  font: 600 13.5px/1.3 'DM Sans', sans-serif; color: #1a472a;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
  overflow: hidden;
}
.kf-product-meta {
  display: flex; align-items: baseline; gap: 8px; margin-top: 5px;
  font-size: 11.5px;
}
.kf-product-price { font: 600 12.5px/1 'JetBrains Mono', monospace; color: #2d7a4a; }
.kf-product-moq { color: #9c978e; }

/* Payment card */
.kf-pay {
  position: relative; overflow: hidden;
  border: 1.5px solid #1a472a; border-radius: 16px; background: #fff;
  margin-top: 10px; max-width: 100%;
  animation: kf-card-in .4s cubic-bezier(.32,.72,0,1) both;
}
.kf-pay-shimmer {
  position: absolute; top: 0; left: 0; right: 0; height: 3px;
  background: linear-gradient(90deg, transparent, #b8860b 40%, #e9d094 50%, #b8860b 60%, transparent);
  animation: kf-shimmer 2s ease-out 1;
}
.kf-pay-body { padding: 16px 18px 18px; }
.kf-pay-title {
  font: 600 14.5px/1 'DM Sans', sans-serif; color: #1a472a;
  margin-bottom: 12px; display: flex; align-items: center; gap: 8px;
}
.kf-pay-title::before { content: '💳'; font-size: 16px; }
.kf-pay-line {
  display: flex; justify-content: space-between; align-items: baseline;
  font-size: 13px; padding: 4px 0;
  color: #6b6860;
}
.kf-pay-line .kf-mono { color: #1a1917; font-weight: 500; }
.kf-pay-line.kf-pay-free .kf-mono { color: #2d7a4a; font-weight: 600; }
.kf-pay-divider {
  border-top: 1px dashed #e5e2dc; margin: 8px 0;
}
.kf-pay-total {
  display: flex; justify-content: space-between; align-items: baseline;
  font: 700 18px/1 'JetBrains Mono', monospace; color: #1a472a;
  margin-top: 4px;
}
.kf-pay-total-label { font: 600 13px/1 'DM Sans', sans-serif; color: #1a1917; }
.kf-pay-btn {
  display: block; width: 100%; margin-top: 14px;
  padding: 13px 20px; border-radius: 12px;
  background: #1a472a; color: #fff; text-align: center;
  text-decoration: none; font: 600 14.5px/1 'DM Sans', sans-serif;
  cursor: pointer; border: none;
  transition: background .15s, transform .12s;
  animation: kf-pulse-ring 1.6s ease-out 3;
}
.kf-pay-btn:hover { background: #2d7a4a; transform: scale(1.01); }
.kf-pay-btn:active { transform: scale(.98); }
.kf-pay-btn:focus-visible { outline: 2px solid #e9d094; outline-offset: 3px; }
.kf-pay-meta {
  margin-top: 8px; font-size: 11px; color: #9c978e; text-align: center;
}

/* Input area */
.kf-input-wrap {
  border-top: 1px solid #f0ede8; background: #fff;
  padding: 10px 12px env(safe-area-inset-bottom, 10px);
}
.kf-form { display: flex; align-items: flex-end; gap: 8px; }
.kf-text {
  flex: 1; resize: none; max-height: 96px; min-height: 36px;
  padding: 9px 12px;
  background: #faf9f7; border: 1px solid #e5e2dc; border-radius: 12px;
  font: 400 14px/1.4 'DM Sans', sans-serif; color: #1a1917;
  outline: none;
  transition: border-color .15s, background .15s;
}
.kf-text:focus { border-color: #1a472a; background: #fff; }
.kf-text::placeholder { color: #b6b1a7; }
.kf-text:disabled { opacity: .55; cursor: not-allowed; }
.kf-send {
  width: 36px; height: 36px; border-radius: 12px; flex-shrink: 0;
  background: #1a472a; border: none; color: #fff; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center;
  transition: background .15s, transform .12s, opacity .15s;
}
.kf-send:hover:not(:disabled) { background: #2d7a4a; transform: scale(1.04); }
.kf-send:active:not(:disabled) { transform: scale(.96); }
.kf-send:disabled { opacity: .35; cursor: not-allowed; }
.kf-send:focus-visible { outline: 2px solid #b8860b; outline-offset: 2px; }
.kf-credit {
  font-size: 10.5px; color: #b6b1a7; text-align: center; margin-top: 6px;
  letter-spacing: .02em;
}
.kf-offline {
  background: #a83232; color: #fff; padding: 6px 12px; font-size: 12px;
  text-align: center;
}

/* Mobile */
@media (max-width: 640px) {
  .kf-trigger { right: 16px; bottom: 16px; padding: 9px 16px 9px 12px; font-size: 13px; }
  .kf-panel {
    right: 0; bottom: 0; left: 0; top: 0;
    width: 100vw; height: 100vh; height: 100dvh;
    border-radius: 0; border: none;
    transform-origin: center bottom;
  }
  .kf-tip { right: 16px; bottom: 64px; }
  .kf-header { padding-top: max(14px, env(safe-area-inset-top, 14px)); }
  .kf-bubble { max-width: 86%; }
}

/* Reduced motion — disable everything except 1-frame opacity */
@media (prefers-reduced-motion: reduce) {
  .kf-trigger, .kf-trigger::before, .kf-avatar::after, .kf-task-spark::after,
  .kf-step.kf-active .kf-step-icon::before, .kf-step.kf-done .kf-step-icon::before,
  .kf-pay-btn, .kf-status .kf-dot, .kf-typing span,
  .kf-product, .kf-pay, .kf-bubble, .kf-task, .kf-action {
    animation: none !important;
    transition: opacity .2s ease !important;
  }
  .kf-pay-shimmer { display: none; }
}

/* ─── Keyframes ─────────────────────────────────────────────── */
@keyframes kf-glow {
  0%, 100% { box-shadow: 0 4px 15px rgba(26,71,42,.22), 0 0 0 1px rgba(255,255,255,.04) inset; }
  50%      { box-shadow: 0 4px 25px rgba(26,71,42,.4),  0 0 0 1px rgba(255,255,255,.06) inset; }
}
@keyframes kf-border-spin {
  to { --kf-angle: 360deg; }
}
@keyframes kf-orbit {
  from { transform: rotate(0deg) translateX(11px) rotate(0deg); }
  to   { transform: rotate(360deg) translateX(11px) rotate(-360deg); }
}
@keyframes kf-orbit-fast {
  from { transform: rotate(0deg) translateX(9px) rotate(0deg); }
  to   { transform: rotate(360deg) translateX(9px) rotate(-360deg); }
}
@keyframes kf-pulse-dot {
  0%, 100% { transform: scale(1); opacity: 1; }
  50%      { transform: scale(1.4); opacity: .7; }
}
@keyframes kf-spin { to { transform: rotate(360deg); } }
@keyframes kf-check-pop {
  0%   { transform: scale(0); }
  60%  { transform: scale(1.25); }
  100% { transform: scale(1); }
}
@keyframes kf-fade-in { from { opacity: 0; } to { opacity: 1; } }
@keyframes kf-fade-up {
  from { opacity: 0; transform: translateY(10px); }
  to   { opacity: 1; transform: translateY(0); }
}
@keyframes kf-slide-left {
  from { opacity: 0; transform: translateX(-12px); }
  to   { opacity: 1; transform: translateX(0); }
}
@keyframes kf-slide-right {
  from { opacity: 0; transform: translateX(12px); }
  to   { opacity: 1; transform: translateX(0); }
}
@keyframes kf-card-in {
  from { opacity: 0; transform: translateX(-12px) scale(.97); }
  to   { opacity: 1; transform: translateX(0) scale(1); }
}
@keyframes kf-shimmer {
  from { transform: translateX(-100%); }
  to   { transform: translateX(100%); }
}
@keyframes kf-pulse-ring {
  0%   { box-shadow: 0 0 0 0 rgba(26,71,42,.4); }
  70%  { box-shadow: 0 0 0 12px rgba(26,71,42,0); }
  100% { box-shadow: 0 0 0 0 rgba(26,71,42,0); }
}
@keyframes kf-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: .35; }
  30%           { transform: translateY(-6px); opacity: 1; }
}
`;

  // ─────────────────────────────────────────────────────────────────
  // Shadow DOM mount
  // ─────────────────────────────────────────────────────────────────
  var host = document.createElement('div');
  host.setAttribute('data-kf-widget', '');
  host.style.cssText = 'all:initial; position:fixed; inset:0; pointer-events:none; z-index:2147483640;';
  var shadow = host.attachShadow({ mode: 'open' });

  var styleEl = document.createElement('style');
  styleEl.textContent = STYLES;
  shadow.appendChild(styleEl);

  var root = document.createElement('div');
  root.className = 'kf-root';
  // Re-enable pointer events inside the root
  root.style.cssText = 'pointer-events:auto;';
  shadow.appendChild(root);

  // ─────────────────────────────────────────────────────────────────
  // DOM build
  // ─────────────────────────────────────────────────────────────────
  // Trigger pill
  var trigger = document.createElement('button');
  trigger.className = 'kf-trigger';
  trigger.type = 'button';
  trigger.setAttribute('aria-label', 'Open Kingdom Foods AI sales assistant');
  trigger.innerHTML =
    '<span class="kf-trigger-icon" aria-hidden="true"></span>' +
    '<span>Kingdom Foods AI</span>';
  root.appendChild(trigger);

  // First-visit tooltip
  var tip = document.createElement('div');
  tip.className = 'kf-tip';
  tip.setAttribute('role', 'note');
  tip.textContent = 'Need help finding products? Ask me anything.';
  root.appendChild(tip);

  // Panel
  var panel = document.createElement('section');
  panel.className = 'kf-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'false');
  panel.setAttribute('aria-label', 'Chat with Kingdom Foods AI');
  panel.setAttribute('aria-hidden', 'true');
  panel.innerHTML = [
    '<header class="kf-header">',
    '  <div class="kf-avatar" aria-hidden="true"></div>',
    '  <div class="kf-meta">',
    '    <div class="kf-name">Kingdom Foods</div>',
    '    <div class="kf-status"><span class="kf-dot" aria-hidden="true"></span><span class="kf-status-text">AI Sales Assistant · Online</span></div>',
    '  </div>',
    '  <button class="kf-close" type="button" aria-label="Close chat">',
    '    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">',
    '      <path d="M5 12h14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
    '    </svg>',
    '  </button>',
    '</header>',
    '<div class="kf-messages" role="log" aria-live="polite" aria-atomic="false"></div>',
    '<div class="kf-input-wrap">',
    '  <form class="kf-form" autocomplete="off">',
    '    <textarea class="kf-text" rows="1" placeholder="Type your message…" aria-label="Type your message"></textarea>',
    '    <button class="kf-send" type="submit" disabled aria-label="Send message">',
    '      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">',
    '        <path d="M4 12h16M14 6l6 6-6 6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    '      </svg>',
    '    </button>',
    '  </form>',
    '  <div class="kf-credit">Powered by Kingdom Foods AI · Hindi &amp; English</div>',
    '</div>',
  ].join('\n');
  root.appendChild(panel);

  document.body.appendChild(host);

  var $messages = panel.querySelector('.kf-messages');
  var $form = panel.querySelector('.kf-form');
  var $text = panel.querySelector('.kf-text');
  var $send = panel.querySelector('.kf-send');
  var $close = panel.querySelector('.kf-close');
  var $statusText = panel.querySelector('.kf-status-text');

  // ─────────────────────────────────────────────────────────────────
  // Tab pulse — favicon canvas + title alternation
  // ─────────────────────────────────────────────────────────────────
  var tabPulse = (function () {
    var origTitle = document.title;
    var origFavicon = (function () {
      var l = document.querySelector('link[rel~="icon"]');
      return l ? l.href : null;
    })();
    var faviconLink = null;
    var canvas = null;
    var ctx = null;
    var rafId = null;
    var titleId = null;
    var angle = 0;

    function getOrCreateLink() {
      var l = document.querySelector('link[rel~="icon"]');
      if (!l) {
        l = document.createElement('link');
        l.rel = 'icon';
        document.head.appendChild(l);
      }
      return l;
    }

    function start() {
      if (REDUCE_MOTION) return;
      faviconLink = getOrCreateLink();
      canvas = document.createElement('canvas');
      canvas.width = 32; canvas.height = 32;
      ctx = canvas.getContext('2d');

      function draw() {
        ctx.clearRect(0, 0, 32, 32);
        // emerald disc
        ctx.beginPath();
        ctx.arc(16, 16, 14, 0, Math.PI * 2);
        ctx.fillStyle = '#1a472a';
        ctx.fill();
        // gold spark
        ctx.fillStyle = '#e9d094';
        ctx.font = 'bold 18px sans-serif';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText('✦', 16, 17);
        // orbiting dot
        angle += 0.18;
        var x = 16 + Math.cos(angle) * 12;
        var y = 16 + Math.sin(angle) * 12;
        ctx.beginPath();
        ctx.arc(x, y, 3, 0, Math.PI * 2);
        ctx.fillStyle = '#4ade80';
        ctx.fill();
        try { faviconLink.href = canvas.toDataURL('image/png'); } catch (_) {}
        rafId = requestAnimationFrame(draw);
      }
      draw();

      titleId = setInterval(function () {
        document.title = document.title.indexOf('✦') === 0
          ? origTitle
          : '✦ Working… — Kingdom Foods';
      }, 1500);
    }

    function stopAndNotify() {
      if (rafId) cancelAnimationFrame(rafId);
      if (titleId) clearInterval(titleId);
      rafId = null; titleId = null;

      if (document.hidden) {
        S.pendingTitleCount += 1;
        document.title = '(' + S.pendingTitleCount + ') ' + origTitle;
        // restore favicon to original
        if (faviconLink && origFavicon) faviconLink.href = origFavicon;

        // optional desktop notification
        if ('Notification' in window) {
          if (Notification.permission === 'granted') {
            try {
              var n = new Notification('Kingdom Foods AI', {
                body: 'I have a recommendation for you.',
                icon: origFavicon || undefined,
              });
              n.onclick = function () {
                window.focus();
                resetTitleOnFocus();
                n.close();
              };
            } catch (_) {}
          } else if (Notification.permission === 'default') {
            // Don't auto-prompt — only on user gesture (panel open). Skip here.
          }
        }
      } else {
        document.title = origTitle;
        if (faviconLink && origFavicon) faviconLink.href = origFavicon;
      }
    }

    function resetTitleOnFocus() {
      S.pendingTitleCount = 0;
      document.title = origTitle;
      if (faviconLink && origFavicon) faviconLink.href = origFavicon;
    }

    // When the user comes back to the tab, clear the (N) count
    window.addEventListener('focus', resetTitleOnFocus);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) resetTitleOnFocus();
    });

    return { start: start, stop: stopAndNotify };
  })();

  // ─────────────────────────────────────────────────────────────────
  // Sound — Web Audio oscillator. Subtle. Off until first user gesture.
  // ─────────────────────────────────────────────────────────────────
  var sfx = (function () {
    function ensureCtx() {
      if (S.soundCtx) return S.soundCtx;
      try {
        var Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return null;
        S.soundCtx = new Ctx();
      } catch (_) { return null; }
      return S.soundCtx;
    }

    function play(freq, dur, gainTop) {
      if (!S.soundOn || REDUCE_MOTION) return;
      var ctx = ensureCtx(); if (!ctx) return;
      try {
        var osc = ctx.createOscillator();
        var gain = ctx.createGain();
        osc.frequency.value = freq;
        osc.type = 'sine';
        gain.gain.setValueAtTime(0.0001, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(gainTop || 0.06, ctx.currentTime + 0.005);
        gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + (dur || 0.05));
        osc.connect(gain).connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + (dur || 0.05) + 0.02);
      } catch (_) {}
    }

    return {
      open:    function () { play(420, 0.10); },
      send:    function () { play(720, 0.04); },
      step:    function (i) { play(560 + i * 50, 0.04, 0.04); },
      payment: function () { play(660, 0.18, 0.08); setTimeout(function(){ play(880, 0.18, 0.06); }, 90); },
      enable:  function () { S.soundOn = true; ensureCtx(); },
    };
  })();

  // ─────────────────────────────────────────────────────────────────
  // Helpers
  // ─────────────────────────────────────────────────────────────────
  function scrollDown() {
    requestAnimationFrame(function () {
      $messages.scrollTop = $messages.scrollHeight;
    });
  }
  function nextFrame() { return new Promise(function (r) { requestAnimationFrame(function () { r(); }); }); }
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function fmtINR(n) { return '₹' + Math.round(n || 0).toLocaleString('en-IN'); }

  // ─────────────────────────────────────────────────────────────────
  // Renderers
  // ─────────────────────────────────────────────────────────────────
  function appendUserMessage(text) {
    var row = document.createElement('div');
    row.className = 'kf-row kf-user';
    var bubble = document.createElement('div');
    bubble.className = 'kf-bubble kf-user-bubble';
    bubble.textContent = text;
    row.appendChild(bubble);
    $messages.appendChild(row);
    scrollDown();
    return row;
  }

  function appendBotRow() {
    var row = document.createElement('div');
    row.className = 'kf-row';
    var avatar = document.createElement('div');
    avatar.className = 'kf-bot-avatar';
    avatar.setAttribute('aria-hidden', 'true');
    row.appendChild(avatar);
    var stack = document.createElement('div');
    stack.style.cssText = 'display:flex; flex-direction:column; gap:6px; min-width:0; flex:1;';
    row.appendChild(stack);
    $messages.appendChild(row);
    return { row: row, stack: stack };
  }

  function appendBotBubble(text, stack) {
    var bubble = document.createElement('div');
    bubble.className = 'kf-bubble kf-bot-bubble';
    bubble.textContent = text;
    stack.appendChild(bubble);
    scrollDown();
    return bubble;
  }

  function appendTyping() {
    var row = appendBotRow();
    var typing = document.createElement('div');
    typing.className = 'kf-typing';
    typing.setAttribute('aria-label', 'Assistant is typing');
    typing.innerHTML = '<span></span><span></span><span></span>';
    row.stack.appendChild(typing);
    scrollDown();
    return row.row;
  }

  function buildProductCards(products, stack) {
    if (!products || !products.length) return;
    var wrap = document.createElement('div');
    wrap.className = 'kf-products';
    var hasExternalSite = !!siteUrl;

    products.slice(0, 4).forEach(function (p, i) {
      var card = document.createElement(hasExternalSite ? 'a' : 'button');
      card.className = 'kf-product';
      card.style.animationDelay = (i * 0.12) + 's';
      var price = fmtINR(p.price) + (p.pack ? ' · ' + p.pack : '');

      if (hasExternalSite) {
        var slug = p.slug || '';
        card.href = slug
          ? siteUrl + '/products/' + encodeURIComponent(slug)
          : siteUrl + '/products';
        card.target = '_blank';
        card.rel = 'noopener noreferrer';
        card.setAttribute('aria-label', (p.name || 'Product') + ', ' + price + ', open product page in new tab');
      } else {
        card.type = 'button';
        card.setAttribute('aria-label', 'Ask about ' + (p.name || 'this product') + ', ' + price);
        card.addEventListener('click', function () {
          var ask = 'Tell me more about ' + (p.name || 'this product') + ' — pricing, pack sizes, MOQ';
          $text.value = ask;
          resizeText();
          $send.disabled = false;
          submit();
        });
      }

      var img = document.createElement('div');
      img.className = 'kf-product-img';
      if (p.image) img.style.backgroundImage = 'url("' + String(p.image).replace(/"/g, '%22') + '")';
      card.appendChild(img);

      var body = document.createElement('div');
      body.className = 'kf-product-body';
      var cat = document.createElement('div');
      cat.className = 'kf-product-cat'; cat.textContent = p.category || 'PRODUCT';
      body.appendChild(cat);
      var name = document.createElement('div');
      name.className = 'kf-product-name'; name.textContent = p.name || '';
      body.appendChild(name);
      var meta = document.createElement('div');
      meta.className = 'kf-product-meta';
      var priceEl = document.createElement('span');
      priceEl.className = 'kf-product-price';
      priceEl.textContent = fmtINR(p.price);
      meta.appendChild(priceEl);
      var moq = document.createElement('span');
      moq.className = 'kf-product-moq';
      moq.textContent = p.moq || (p.pack ? p.pack : '');
      meta.appendChild(moq);
      body.appendChild(meta);

      // Affordance line — different verb depending on whether we link out
      // or send a chat message
      var affordance = document.createElement('div');
      affordance.className = 'kf-product-cta';
      affordance.textContent = hasExternalSite ? 'View details →' : 'Ask about this →';
      body.appendChild(affordance);

      card.appendChild(body);
      wrap.appendChild(card);
    });
    stack.appendChild(wrap);
    scrollDown();
  }

  function buildPaymentCard(p, stack) {
    if (!p) return;
    var amount = Number(p.amount || 0);
    var wrap = document.createElement('div');
    wrap.className = 'kf-pay';
    var shimmer = document.createElement('div');
    shimmer.className = 'kf-pay-shimmer'; shimmer.setAttribute('aria-hidden', 'true');
    wrap.appendChild(shimmer);

    var body = document.createElement('div');
    body.className = 'kf-pay-body';
    body.innerHTML =
      '<div class="kf-pay-title">Your Order Summary</div>' +
      '<div class="kf-pay-line"><span>' + escapeHtml(p.description || 'Order') + '</span><span class="kf-mono">' + fmtINR(amount) + '</span></div>' +
      '<div class="kf-pay-divider" aria-hidden="true"></div>' +
      '<div class="kf-pay-total"><span class="kf-pay-total-label">Total</span><span class="kf-mono kf-pay-total-amount">₹0</span></div>';

    var btn = document.createElement('a');
    btn.className = 'kf-pay-btn';
    btn.href = p.url || '#';
    btn.target = '_blank';
    btn.rel = 'noopener noreferrer';
    btn.textContent = 'Pay ' + fmtINR(amount) + ' via Razorpay';
    btn.setAttribute('aria-label', 'Pay ' + fmtINR(amount) + ' via Razorpay (opens in new tab)');
    body.appendChild(btn);

    var meta = document.createElement('div');
    meta.className = 'kf-pay-meta';
    meta.innerHTML = '🔒 Secure payment · Link expires in ' + (p.expires_in_minutes || 30) + ' min';
    body.appendChild(meta);

    wrap.appendChild(body);
    stack.appendChild(wrap);
    scrollDown();

    // count-up
    var totalEl = wrap.querySelector('.kf-pay-total-amount');
    if (REDUCE_MOTION) {
      totalEl.textContent = fmtINR(amount);
    } else {
      animateCount(totalEl, 0, amount, 600);
    }
    sfx.payment();
  }

  function buildActions(actions, stack) {
    if (!actions || !actions.length) return;
    var wrap = document.createElement('div');
    wrap.className = 'kf-actions';
    wrap.setAttribute('role', 'group');
    wrap.setAttribute('aria-label', 'Suggested actions');
    actions.forEach(function (a, i) {
      var btn = document.createElement('button');
      btn.className = 'kf-action';
      btn.type = 'button';
      btn.textContent = a.label;
      btn.style.animationDelay = (0.1 + i * 0.08) + 's';
      btn.addEventListener('click', function () {
        if (a.message === 'OPEN_WHATSAPP') {
          window.open(
            'https://wa.me/918800804580?text=' + encodeURIComponent('Hi Kingdom Foods, I was chatting on the website.'),
            '_blank',
            'noopener,noreferrer'
          );
          return;
        }
        if (a.message === 'OPEN_EMAIL') {
          location.href = 'mailto:contact@just2eat.com';
          return;
        }
        $text.value = a.message || a.label;
        $send.disabled = false;
        submit();
      });
      wrap.appendChild(btn);
    });
    stack.appendChild(wrap);
    scrollDown();
  }

  function animateCount(el, from, to, duration) {
    var start = performance.now();
    function tick(now) {
      var p = Math.min(1, (now - start) / duration);
      var eased = 1 - Math.pow(1 - p, 3); // ease-out cubic
      el.textContent = fmtINR(from + (to - from) * eased);
      if (p < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c];
    });
  }

  // ─────────────────────────────────────────────────────────────────
  // Agent task system
  // ─────────────────────────────────────────────────────────────────
  function getTaskSteps(message) {
    var m = (message || '').toLowerCase();
    if (/\b(order|book|confirm)\b|chahiye|karo|le\s*lo/.test(m)) {
      return ['Processing your request', 'Verifying product availability', 'Calculating bulk pricing', 'Computing delivery charges', 'Generating payment link'];
    }
    if (/\b(price|rate|cost|quote)\b|kitna|kya rate|rate kya/.test(m)) {
      return ['Checking current prices', 'Loading bulk pricing tiers', 'Preparing price comparison'];
    }
    if (/\b(sample|trial|try|test)\b/.test(m)) {
      return ['Noted — sample request', 'Checking sample availability', 'Preparing sample options'];
    }
    if (/\b(product|catalog|menu|range|show|list)\b|kya hai|kuch|recommend/.test(m)) {
      return ['Understanding your request', 'Searching 526 products', 'Matching to your kitchen', 'Preparing product cards'];
    }
    return ['Understanding your message', 'Loading relevant information', 'Preparing recommendation'];
  }

  function buildTaskCard(steps, stack) {
    var wrap = document.createElement('div');
    wrap.className = 'kf-task';
    wrap.setAttribute('aria-live', 'polite');

    var head = document.createElement('div');
    head.className = 'kf-task-head';
    head.innerHTML = '<span class="kf-task-spark" aria-hidden="true"></span><span>Working on your request…</span>';
    wrap.appendChild(head);

    var stepsWrap = document.createElement('div');
    stepsWrap.className = 'kf-task-steps';
    var stepEls = steps.map(function (label) {
      var s = document.createElement('div');
      s.className = 'kf-step';
      s.innerHTML = '<span class="kf-step-icon" aria-hidden="true"></span><span>' + escapeHtml(label) + '</span>';
      stepsWrap.appendChild(s);
      return s;
    });
    wrap.appendChild(stepsWrap);

    var bar = document.createElement('div');
    bar.className = 'kf-task-bar';
    var fill = document.createElement('div');
    fill.className = 'kf-task-bar-fill';
    bar.appendChild(fill);
    wrap.appendChild(bar);

    stack.appendChild(wrap);
    scrollDown();
    return { wrap: wrap, stepEls: stepEls, fill: fill };
  }

  // Returns a controller that animates the steps. The controller exposes:
  //   minimumTime  Promise that resolves when at least minMs has elapsed
  //   complete()   advances any remaining steps quickly, returns Promise resolved when done
  //   stop()       freezes animation
  function animateSteps(taskUI, steps, opts) {
    opts = opts || {};
    var perStepNormal = opts.perStep || 420;
    var perStepFast = opts.fastPerStep || 180;
    var minTotal = opts.minTotal || 1800;

    if (REDUCE_MOTION) {
      // Mark all done immediately
      taskUI.stepEls.forEach(function (el) { el.classList.add('kf-done', 'kf-faded'); });
      taskUI.fill.style.width = '100%';
      return {
        minimumTime: Promise.resolve(),
        complete: function () { return Promise.resolve(); },
      };
    }

    var idx = -1;
    var doneFlag = false;
    var startTime = performance.now();

    function setActive(i) {
      // mark previous done + faded
      if (i > 0) {
        var prev = taskUI.stepEls[i - 1];
        prev.classList.remove('kf-active');
        prev.classList.add('kf-done', 'kf-faded');
        sfx.step(i);
      }
      if (i < taskUI.stepEls.length) {
        taskUI.stepEls[i].classList.add('kf-active');
        var pct = ((i + 1) / taskUI.stepEls.length) * 90; // leave 10% for "preparing"
        taskUI.fill.style.width = pct + '%';
      }
    }

    function advance(perStep) {
      return new Promise(function (resolve) {
        function step() {
          idx += 1;
          if (idx >= taskUI.stepEls.length) {
            // All steps cycled (current is past end) — keep last spinning
            resolve();
            return;
          }
          setActive(idx);
          if (idx === taskUI.stepEls.length - 1) {
            // last step stays spinning; resolve when complete() is called
            resolve();
            return;
          }
          setTimeout(step, perStep);
        }
        step();
      });
    }

    var advancing = advance(perStepNormal);
    var minTimer = wait(minTotal);

    function complete() {
      if (doneFlag) return Promise.resolve();
      doneFlag = true;
      // Fast-forward any remaining (we may have stalled on the last step)
      var remaining = taskUI.stepEls.length - 1 - idx;
      var p = Promise.resolve();
      if (remaining > 0) {
        p = advance(perStepFast);
      }
      return p.then(function () {
        // Mark the last step done
        var last = taskUI.stepEls[taskUI.stepEls.length - 1];
        if (last) {
          last.classList.remove('kf-active');
          last.classList.add('kf-done');
          sfx.step(taskUI.stepEls.length);
        }
        taskUI.fill.style.width = '100%';
        return wait(150);
      });
    }

    return {
      minimumTime: minTimer,
      complete: complete,
    };
  }

  function collapseTaskCard(taskUI) {
    var wrap = taskUI.wrap;
    var h = wrap.getBoundingClientRect().height;
    wrap.style.maxHeight = h + 'px';
    return nextFrame().then(function () {
      wrap.classList.add('kf-collapsing');
      return wait(320);
    }).then(function () {
      wrap.remove();
    });
  }

  // ─────────────────────────────────────────────────────────────────
  // API call
  // ─────────────────────────────────────────────────────────────────
  function callApi(message) {
    var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var timeoutId = setTimeout(function () { try { controller && controller.abort(); } catch (_) {} }, 60000);
    return fetch(apiBase, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        conversation_id: S.convId,
        message: message,
        metadata: {
          page_url: location.pathname + location.search,
          referrer: document.referrer,
          timestamp: new Date().toISOString(),
        },
      }),
      signal: controller ? controller.signal : undefined,
    }).then(function (r) {
      clearTimeout(timeoutId);
      if (r.status === 429) {
        return r.json().then(function (j) { var e = new Error('rate_limited'); e.isRate = true; e.payload = j; throw e; });
      }
      if (!r.ok) { var e2 = new Error('http_' + r.status); e2.status = r.status; throw e2; }
      return r.json();
    });
  }

  // ─────────────────────────────────────────────────────────────────
  // Submit flow — the heart of the agent UX
  // ─────────────────────────────────────────────────────────────────
  function submit() {
    if (S.sending) return;
    var raw = ($text.value || '').trim();
    if (!raw) return;

    if (raw === 'OPEN_WHATSAPP') {
      window.open('https://wa.me/918800804580?text=' + encodeURIComponent('Hi Kingdom Foods, I was chatting on the website.'), '_blank', 'noopener,noreferrer');
      $text.value = ''; resizeText(); return;
    }
    if (raw === 'OPEN_EMAIL') { location.href = 'mailto:contact@just2eat.com'; $text.value = ''; resizeText(); return; }

    if (!S.online) {
      var row = appendBotRow();
      appendBotBubble("You're offline. Reconnect or WhatsApp +91 8800804580 (India) / +1 (555) 749-5990 (Intl).", row.stack);
      buildActions([{ label: 'Open WhatsApp', message: 'OPEN_WHATSAPP' }], row.stack);
      return;
    }

    // 1. show user message
    appendUserMessage(raw);
    $text.value = ''; resizeText();
    sfx.send();

    // 2. block input
    S.sending = true; S.state = STATES.THINKING;
    $send.disabled = true;
    setStatus('Working on it…');

    // 3. spawn task card
    var bot = appendBotRow();
    var steps = getTaskSteps(raw);
    var taskUI = buildTaskCard(steps, bot.stack);

    // 4. begin animation + tab pulse + API in parallel
    if (document.hidden) tabPulse.start();
    else if (raw.length > 12) tabPulse.start(); // also pulse if a substantive task is running and user might tab away

    var animator = animateSteps(taskUI, steps);

    // Simple intent check — for true greetings/short messages we still do the
    // task-card path (user already saw it appear) but with a shorter min time.
    var apiPromise = callApi(raw);

    Promise.all([apiPromise.catch(function (e) { return { __error: e }; }), animator.minimumTime])
      .then(function (results) {
        var resp = results[0];
        return animator.complete().then(function () { return resp; });
      })
      .then(function (resp) {
        return collapseTaskCard(taskUI).then(function () { return resp; });
      })
      .then(function (resp) {
        S.state = STATES.RESPONDING;
        if (resp && resp.__error) {
          handleError(resp.__error, raw, bot.stack);
        } else {
          renderResponse(resp, bot.stack);
        }
      })
      .catch(function (err) {
        // Should never hit this since we caught above, but just in case
        handleError(err, raw, bot.stack);
      })
      .then(function () {
        tabPulse.stop();
        S.sending = false; S.state = STATES.READY;
        $send.disabled = !$text.value.trim();
        setStatus('AI Sales Assistant · Online');
        $text.focus();
      });
  }

  function renderResponse(data, stack) {
    if (!data) {
      appendBotBubble('Sorry — please try again.', stack);
      return;
    }
    if (data.conversation_id) {
      S.convId = data.conversation_id;
      try { localStorage.setItem('kf_conv_id', S.convId); } catch (_) {}
    }
    var text = data.reply || 'Sorry — please try again.';
    appendBotBubble(text, stack);
    if (data.products && data.products.length) buildProductCards(data.products, stack);
    if (data.payment) buildPaymentCard(data.payment, stack);
    if (data.actions && data.actions.length) buildActions(data.actions, stack);
  }

  function handleError(err, originalMessage, stack) {
    if (err && err.isRate) {
      var msg = (err.payload && (err.payload.message || err.payload.error)) ||
        'Bahut saare messages aa rahe hain. Thoda ruk kar try karo ya seedha call karo: 8800804580 / 15557495990';
      appendBotBubble(msg, stack);
      buildActions([{ label: 'Open WhatsApp', message: 'OPEN_WHATSAPP' }], stack);
      return;
    }
    appendBotBubble(
      "I'm offline for a moment. Please WhatsApp +91 8800804580 (India) or +1 (555) 749-5990 (Intl), or email contact@just2eat.com — our team replies within an hour during business hours.",
      stack
    );
    buildActions(
      [
        { label: 'Open WhatsApp', message: 'OPEN_WHATSAPP' },
        { label: 'Try again', message: originalMessage },
      ],
      stack
    );
  }

  function setStatus(text) {
    if ($statusText) $statusText.textContent = text;
  }

  // ─────────────────────────────────────────────────────────────────
  // Welcome / greeting flow (shown the first time the panel opens)
  // ─────────────────────────────────────────────────────────────────
  function sendInitialGreeting() {
    if (S.greeted) return;
    S.greeted = true;

    // Show a quick "thinking" placeholder for drama (even though greeting is fast)
    var bot = appendBotRow();
    var taskUI = buildTaskCard(['Loading your assistant'], bot.stack);
    var animator = animateSteps(taskUI, ['Loading your assistant'], { minTotal: 600, perStep: 600 });

    Promise.all([callApi('__greeting__').catch(function (e) { return { __error: e }; }), animator.minimumTime])
      .then(function (rs) {
        return animator.complete().then(function () { return rs[0]; });
      })
      .then(function (resp) {
        return collapseTaskCard(taskUI).then(function () { return resp; });
      })
      .then(function (resp) {
        if (!resp || resp.__error) {
          appendBotBubble('Welcome to Kingdom Foods. How can I help your kitchen today?', bot.stack);
          buildActions(
            [
              { label: 'I run a hotel',      message: 'I run a hotel — what should I start with?' },
              { label: 'I run a restaurant', message: 'I run a restaurant — recommend bestsellers' },
              { label: 'Cloud kitchen',      message: 'I operate a cloud kitchen' },
              { label: 'Just browsing',      message: 'Show me your bestsellers' },
            ],
            bot.stack
          );
          return;
        }
        renderResponse(resp, bot.stack);
      });
  }

  // ─────────────────────────────────────────────────────────────────
  // Open / close
  // ─────────────────────────────────────────────────────────────────
  function openPanel() {
    if (S.open) return;
    S.lastFocus = document.activeElement;
    S.open = true;
    S.state = STATES.OPENING;
    trigger.classList.add('kf-hidden');
    panel.classList.add('kf-open');
    panel.setAttribute('aria-hidden', 'false');
    sfx.enable(); sfx.open();
    hideTip();

    // Request notification permission gracefully on first open (user gesture)
    if ('Notification' in window && Notification.permission === 'default') {
      try { Notification.requestPermission(); } catch (_) {}
    }

    setTimeout(function () { $text && $text.focus(); }, 250);

    if (!S.greeted) sendInitialGreeting();
    S.state = STATES.READY;
    S.panelEverOpened = true;
  }

  function closePanel() {
    if (!S.open) return;
    S.open = false;
    S.state = STATES.CLOSING;
    panel.classList.remove('kf-open');
    panel.setAttribute('aria-hidden', 'true');
    setTimeout(function () {
      trigger.classList.remove('kf-hidden');
      S.state = STATES.IDLE;
      if (S.lastFocus && typeof S.lastFocus.focus === 'function') {
        try { S.lastFocus.focus(); } catch (_) {}
      } else {
        trigger.focus();
      }
    }, 280);
  }

  // ─────────────────────────────────────────────────────────────────
  // Tooltip nudge (first visit, after greetDelay)
  // ─────────────────────────────────────────────────────────────────
  function showTip() {
    if (S.panelEverOpened) return;
    if (localStorage.getItem('kf_tip_seen')) return;
    tip.classList.add('kf-show');
    setTimeout(hideTip, 5000);
  }
  function hideTip() {
    tip.classList.remove('kf-show');
    try { localStorage.setItem('kf_tip_seen', '1'); } catch (_) {}
  }

  // ─────────────────────────────────────────────────────────────────
  // Mobile: swipe down on header to close
  // ─────────────────────────────────────────────────────────────────
  (function attachSwipe() {
    var header = panel.querySelector('.kf-header');
    if (!header) return;
    var startY = null;
    header.addEventListener('touchstart', function (e) {
      if (window.innerWidth >= 640) return;
      startY = e.touches[0].clientY;
    }, { passive: true });
    header.addEventListener('touchmove', function (e) {
      if (startY == null) return;
      var dy = e.touches[0].clientY - startY;
      if (dy > 100) {
        startY = null;
        closePanel();
      }
    }, { passive: true });
    header.addEventListener('touchend', function () { startY = null; }, { passive: true });
  })();

  // ─────────────────────────────────────────────────────────────────
  // visualViewport keyboard handling (mobile)
  // ─────────────────────────────────────────────────────────────────
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', function () {
      if (S.open && window.innerWidth < 640) {
        // Keep input visible above the keyboard
        scrollDown();
      }
    });
  }

  // ─────────────────────────────────────────────────────────────────
  // Wire events
  // ─────────────────────────────────────────────────────────────────
  trigger.addEventListener('click', openPanel);
  $close.addEventListener('click', closePanel);

  function resizeText() {
    $text.style.height = '0px';
    $text.style.height = Math.min($text.scrollHeight, 96) + 'px';
  }
  $text.addEventListener('input', function () {
    $send.disabled = S.sending || !$text.value.trim();
    resizeText();
  });
  $text.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
  });
  $form.addEventListener('submit', function (e) { e.preventDefault(); submit(); });

  document.addEventListener('keydown', function (e) {
    if (S.open && e.key === 'Escape') { e.preventDefault(); closePanel(); }
  });

  window.addEventListener('online', function () { S.online = true; });
  window.addEventListener('offline', function () { S.online = false; });

  // External programmatic open
  window.addEventListener('kf:openChat', function (e) {
    openPanel();
    if (e && e.detail && e.detail.message) {
      setTimeout(function () {
        $text.value = e.detail.message;
        resizeText();
        $send.disabled = false;
        $text.focus();
      }, 320);
    }
  });

  // First-visit tooltip (after delay)
  setTimeout(showTip, isNaN(greetDelay) ? 8000 : greetDelay);

  // Done.
})();
