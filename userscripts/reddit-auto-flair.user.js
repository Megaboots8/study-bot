// ==UserScript==
// @name         study-bot Reddit auto-flair + auto-post (title-IPC)
// @namespace    https://github.com/Megaboots8/study-bot
// @version      0.8.3
// @description  When study-bot opens a Reddit submit page, this userscript guides the full submission flow: selects the flair, then advertises each button's exact screen coordinates via document.title so Python can OS-click them with isTrusted=true.  The title format is [SBP:<phase>:<x>,<y>]; Python polls the window title and fires one OS-level click per phase.  v0.8.3: re-published as 0.8.3 to make it obvious in Tampermonkey when the v0.8.2 dialog-detection fix is live (no functional change vs 0.8.2).
// @include      *://*.reddit.com/r/*/submit*
// @include      *://www.reddit.com/r/*/submit*
// @include      *://sh.reddit.com/r/*/submit*
// @include      *://new.reddit.com/r/*/submit*
// @match        *://*.reddit.com/*
// @run-at       document-end
// @grant        none
// ==/UserScript==

/*
 * Title-IPC protocol
 * ------------------
 * Phases emitted by this script (via document.title):
 *
 *   [SBP:add:<x>,<y>]     Flair dialog Add button is ready at physical pixel (x,y)
 *   [SBP:post:<x>,<y>]    Composer Post button is ready
 *   [SBP:submit:<x>,<y>]  Warning-dialog "Submit without editing" is ready
 *   [SBP:done]            Submission complete or no further action needed
 *   [SBP:none]            An expected button was not found (Python treats as timeout)
 *
 * A 250 ms setInterval keeps the current marker in the title so Reddit's
 * own SPA title updates cannot strip it.
 *
 * Gating
 * ------
 *   _autoflair=<name>  URL param — flair to apply (same as before v0.8)
 *   _autopost=true     URL param — if present, script continues through
 *                      Post and Submit-without-editing phases after flair.
 *                      If absent, script stops after advertising [SBP:add].
 */

(async () => {
    'use strict';

    const log  = (...a) => console.log('[study-bot]', ...a);
    const warn = (...a) => console.warn('[study-bot]', ...a);

    log('v0.8 loaded; href =', location.href);

    if (!/\/submit/.test(location.pathname)) {
        log('not a submit page; doing nothing');
        return;
    }

    const params    = new URLSearchParams(location.search);
    const flairName = params.get('_autoflair');
    const autoPost  = params.get('_autopost') === 'true';

    if (!flairName) {
        log('no _autoflair query param; doing nothing');
        return;
    }
    log('flair =', flairName, '| autoPost =', autoPost);

    // ── Helpers ─────────────────────────────────────────────────────────────

    const sleep = ms => new Promise(r => setTimeout(r, ms));

    /** Walk document tree including all open shadow roots. */
    function* deepQueryAll(selector, root = document) {
        if (root.querySelectorAll) {
            for (const el of root.querySelectorAll(selector)) yield el;
        }
        const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
        for (const el of all) {
            if (el.shadowRoot) yield* deepQueryAll(selector, el.shadowRoot);
        }
    }

    function visibleText(el) {
        const al = el.getAttribute && el.getAttribute('aria-label');
        if (al) return al.trim();
        return (el.innerText || el.textContent || '').trim();
    }

    function debugSnippet(el) {
        if (!el) return '<null>';
        return `<${el.tagName.toLowerCase()} aria-label="${el.getAttribute && el.getAttribute('aria-label') || ''}"> ${(el.outerHTML || '').slice(0, 120)}`;
    }

    const CLICKABLE = 'button, [role="button"], a[role="button"], label[for], faceplate-radio-input';

    function findExactIn(root, text) {
        const w = text.toLowerCase();
        const nodes = root.querySelectorAll ? root.querySelectorAll(CLICKABLE) : [];
        for (const el of nodes) if (visibleText(el).toLowerCase() === w) return el;
        return null;
    }

    /** Dispatch a full isTrusted-like pointer+mouse+click chain. */
    function realClick(el) {
        if (!el) return;
        const r  = el.getBoundingClientRect ? el.getBoundingClientRect() : { left:0, top:0, width:0, height:0 };
        const cx = r.left + r.width  / 2;
        const cy = r.top  + r.height / 2;
        const init = { bubbles:true, cancelable:true, composed:true, view:window,
                       button:0, buttons:1, clientX:cx, clientY:cy,
                       pointerType:'mouse', isPrimary:true };
        for (const [t,Ev] of [['pointerdown',PointerEvent],['mousedown',MouseEvent],
                               ['pointerup',PointerEvent],['mouseup',MouseEvent],
                               ['click',MouseEvent]]) {
            try { el.dispatchEvent(new Ev(t, init)); } catch {}
        }
    }

    async function waitFor(pred, { timeout = 12000, interval = 200 } = {}) {
        const t0 = Date.now();
        while (Date.now() - t0 < timeout) {
            try { const r = pred(); if (r) return r; } catch {}
            await sleep(interval);
        }
        return null;
    }

    // ── Title-IPC state ──────────────────────────────────────────────────────

    let _currentMarker = '';        // e.g. '[SBP:add:1200,700]'
    let _origTitle     = '';        // captured once before we start mutating

    /**
     * Compute the absolute physical-pixel screen position of the centre of `el`.
     *
     * Converts CSS pixels -> physical pixels via devicePixelRatio and adds the
     * window's screen offset plus the browser chrome height (title bar + toolbar).
     * On a 100% DPR / 100% Windows scaling setup DPR is 1 and this is a
     * straight viewport-to-screen mapping.
     */
    function buttonScreenPos(el) {
        el.scrollIntoView({ block: 'center', behavior: 'instant' });
        // Force layout so getBoundingClientRect reflects the post-scroll position.
        // eslint-disable-next-line no-unused-expressions
        el.offsetHeight;
        const rect    = el.getBoundingClientRect();
        const dpr     = window.devicePixelRatio || 1;
        const chromeH = window.outerHeight - window.innerHeight;
        const cssX    = rect.left + rect.width  / 2;
        const cssY    = rect.top  + rect.height / 2;
        return {
            x: Math.round((window.screenX + cssX)          * dpr),
            y: Math.round((window.screenY + chromeH + cssY) * dpr),
        };
    }

    function setMarker(phase, pos) {
        if (phase === 'done' || phase === 'none') {
            _currentMarker = `[SBP:${phase}]`;
        } else {
            _currentMarker = `[SBP:${phase}:${pos.x},${pos.y}]`;
        }
        applyMarker();
        log('title marker set:', _currentMarker);
    }

    function applyMarker() {
        if (!_currentMarker) return;
        // Strip any existing marker then prepend the current one.
        const bare = (document.title || '').replace(/\[SBP:[^\]]*\]\s*/g, '').trim();
        document.title = `${_currentMarker} ${bare}`;
    }

    // Keep the marker alive even when Reddit's SPA rewrites document.title.
    setInterval(applyMarker, 250);

    // Capture the page title before we add our markers, for log clarity.
    _origTitle = (document.title || '').replace(/\[SBP:[^\]]*\]\s*/g, '').trim();

    // ── Step 1: open the flair dialog ────────────────────────────────────────

    const addFlairBtn = await waitFor(() => {
        for (const el of deepQueryAll('#reddit-post-flair-button')) return el;
        for (const el of deepQueryAll(CLICKABLE)) {
            const al = (el.getAttribute('aria-label') || '').toLowerCase();
            if (al === 'add flair' || al === 'flair' || al.includes('flair and tags')) return el;
        }
        return null;
    });

    if (!addFlairBtn) {
        warn('no "Add flair and tags" button found');
        setMarker('none');
        return;
    }
    log('clicking flair opener:', debugSnippet(addFlairBtn));
    realClick(addFlairBtn);

    // ── Step 2: wait for dialog, select the radio ────────────────────────────

    const dialog = await waitFor(() => {
        for (const el of deepQueryAll('[role="dialog"], [role="menu"], [role="listbox"], faceplate-dialog, faceplate-menu')) {
            const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
            if (r && r.width > 0 && r.height > 0) return el;
        }
        return null;
    }, { timeout: 8000 });

    if (!dialog) {
        warn('flair dialog did not open');
        setMarker('none');
        return;
    }
    log('flair dialog opened:', debugSnippet(dialog));

    function findOptionInDialog(text) {
        const w = text.toLowerCase();
        const candidates = dialog.querySelectorAll(
            'button, [role="option"], [role="menuitem"], [role="radio"], faceplate-radio-input, label'
        );
        let partial = null;
        for (const el of candidates) {
            const t = visibleText(el).toLowerCase();
            if (!t) continue;
            if (t === w) return el;
            if (!partial && t.includes(w)) partial = el;
        }
        return partial;
    }

    const option = await waitFor(() => findOptionInDialog(flairName));
    if (!option) {
        warn('no flair option matched:', flairName);
        log('available options:', Array.from(dialog.querySelectorAll(
            'button, [role="option"], [role="menuitem"], [role="radio"], faceplate-radio-input, label'
        )).map(visibleText).filter(Boolean));
        setMarker('none');
        return;
    }
    log('selecting radio:', debugSnippet(option));
    realClick(option);
    try { option.dispatchEvent(new Event('change', { bubbles:true, composed:true })); } catch {}
    try { option.dispatchEvent(new Event('input',  { bubbles:true, composed:true })); } catch {}

    // ── Step 3: locate Add button in dialog; advertise [SBP:add] ─────────────

    // The dialog's commit button is typically the LAST button (or the one
    // labelled "Add" / "Apply" / "Save").  We look for it by text first,
    // then fall back to the last <button> inside the dialog.
    function findAddInDialog() {
        // Prefer exact text matches: "Add", "Apply", "Save".
        for (const label of ['add', 'apply', 'save', 'done']) {
            const el = findExactIn(dialog, label);
            if (el) return el;
        }
        // Fallback: last <button> inside the dialog (likely the primary action).
        const btns = Array.from(dialog.querySelectorAll('button'));
        return btns.length ? btns[btns.length - 1] : null;
    }

    const addBtn = await waitFor(findAddInDialog, { timeout: 5000 });
    if (!addBtn) {
        warn('could not locate Add button inside dialog; Python will have to click manually');
        setMarker('none');
        return;
    }
    log('found Add button:', debugSnippet(addBtn));
    await sleep(200);  // let the radio selection animation settle
    const addPos = buttonScreenPos(addBtn);
    log('Add button screen pos:', addPos);
    setMarker('add', addPos);

    if (!autoPost) {
        log('_autopost not set; done after advertising Add');
        return;
    }

    // ── Step 4: wait for flair dialog to close; advertise [SBP:post] ─────────

    // BUG-FIX (v0.8.1): do NOT track the original `dialog` reference.
    // After realClick(option) + dispatchEvent('change'), Reddit's Lit
    // framework re-renders the dialog into a DIFFERENT DOM node.  The old
    // reference becomes detached (document.contains returns false) even
    // though the flair dialog is still visually open, causing a false
    // "dialog gone" signal and skipping Python's Add click entirely.
    //
    // Instead, we re-query for visible flair radio inputs on every tick.
    // These only exist while the dialog is genuinely open; they disappear
    // when a real OS-level click on Add commits the form and Reddit closes
    // the dialog for real.
    const dialogGone = await waitFor(() => {
        // Primary check: any visible flair radio input means dialog is open.
        for (const r of deepQueryAll(
            '[id^="post-flair-radio-input"], faceplate-radio-input[name="flairId"]'
        )) {
            const rect = r.getBoundingClientRect ? r.getBoundingClientRect() : null;
            if (rect && rect.width > 0 && rect.height > 0) return null;  // still open
        }
        // Defensive fallback: if Reddit renames radio IDs, treat any visible
        // faceplate-dialog that still contains buttons as "still open".
        for (const d of deepQueryAll('faceplate-dialog, [role="dialog"]')) {
            const rect = d.getBoundingClientRect ? d.getBoundingClientRect() : null;
            if (rect && rect.width > 50 && rect.height > 50
                    && d.querySelector && d.querySelector('button')) {
                return null;  // still open
            }
        }
        return true;  // no visible flair radios or flair-dialog found → closed
    }, { timeout: 30000, interval: 300 });

    if (!dialogGone) {
        warn('flair dialog did not close within 20s; Python may not have clicked Add');
        setMarker('none');
        return;
    }
    log('flair dialog closed; looking for Post button');

    // Find the composer's Post button.  Reddit's shreddit composer uses a
    // custom element <shreddit-composer> with a shadow root containing the
    // action row.  We try specific selectors first, then fall back to
    // a text match for "Post" among all visible buttons.
    async function findPostButton() {
        // Strategy A: shreddit-specific submit button
        for (const el of deepQueryAll('shreddit-composer-submit-button, shreddit-submit-button, [slot="submit-button"] button')) {
            if (visibleText(el).toLowerCase() === 'post') return el;
        }
        // Strategy B: any button labelled "Post" that is visible on screen
        for (const el of deepQueryAll('button')) {
            if (visibleText(el).toLowerCase() === 'post') {
                const r = el.getBoundingClientRect();
                if (r.width > 0 && r.height > 0) return el;
            }
        }
        // Strategy C: look for the action row container and pick "Post" from it
        for (const el of deepQueryAll('[data-testid="post-submit-button"], [data-post-submit], [aria-label="Post"]')) {
            return el;
        }
        return null;
    }

    const postBtn = await waitFor(findPostButton, { timeout: 10000 });
    if (!postBtn) {
        warn('Post button not found in composer');
        setMarker('none');
        return;
    }
    log('found Post button:', debugSnippet(postBtn));
    const postPos = buttonScreenPos(postBtn);
    log('Post button screen pos:', postPos);
    setMarker('post', postPos);

    // ── Step 5: after Python clicks Post, watch for warning dialog or nav ────

    // Bug fixed in v0.8.2: rather than looking for a dialog element with
    // text "may break" / "rules" (Reddit's rule-warning dialog is rendered
    // with neither role="dialog" nor faceplate-dialog and has content that
    // deepQueryAll can't penetrate), we look DIRECTLY for the button we
    // would click — "Submit without editing".  If a visible button with
    // that label exists, the warning dialog is up.  This single detection
    // doubles as the click target.
    const initialHref = location.href;

    function findSubmitWithoutEditingButton() {
        const labels = ['submit without editing', 'submit anyway', 'post anyway'];
        // Strategy A: visible-text match on any button-like element.
        for (const el of deepQueryAll('button, [role="button"], a[role="button"]')) {
            const t = visibleText(el).toLowerCase();
            if (!t) continue;
            for (const label of labels) {
                if (t === label || t.includes(label)) {
                    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                    if (r && r.width > 0 && r.height > 0) return el;
                }
            }
        }
        // Strategy B: aria-label match across all elements (covers buttons
        // whose visible text is rendered via slot/shadow content).
        for (const el of deepQueryAll('[aria-label]')) {
            const al = (el.getAttribute('aria-label') || '').toLowerCase();
            for (const label of labels) {
                if (al === label || al.includes(label)) {
                    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
                    if (r && r.width > 0 && r.height > 0) return el;
                }
            }
        }
        return null;
    }

    const postResult = await waitFor(() => {
        // Navigation away from /submit/ means the post went through directly.
        if (location.href !== initialHref && !/\/submit/.test(location.pathname)) {
            return 'navigated';
        }
        // Otherwise, look for the warning dialog's signature button.
        const btn = findSubmitWithoutEditingButton();
        if (btn) return btn;
        return null;
    }, { timeout: 30000, interval: 300 });

    if (!postResult) {
        warn('no navigation or "Submit without editing" button after Post click within 30s');
        setMarker('done');  // best effort; user can handle manually
        return;
    }

    if (postResult === 'navigated') {
        log('post submitted without warning dialog; done');
        setMarker('done');
        return;
    }

    // postResult IS the "Submit without editing" button.
    log('warning dialog detected; Submit-without-editing:', debugSnippet(postResult));
    const submitPos = buttonScreenPos(postResult);
    log('Submit-without-editing screen pos:', submitPos);
    setMarker('submit', submitPos);

    // Wait for warning dialog to close (Python clicked Submit-without-editing).
    // Detected by: button no longer visible OR navigation occurred.  Re-query
    // each tick so a Lit re-render (cf. v0.8.1 bug) doesn't fool us.
    await waitFor(() => {
        if (location.href !== initialHref && !/\/submit/.test(location.pathname)) return true;
        if (!findSubmitWithoutEditingButton()) return true;
        return null;
    }, { timeout: 15000, interval: 300 });

    log('submission complete');
    setMarker('done');
})();
