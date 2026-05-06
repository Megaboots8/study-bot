// ==UserScript==
// @name         study-bot Reddit auto-flair
// @namespace    https://github.com/Megaboots8/study-bot
// @version      0.7.0
// @description  When study-bot opens a Reddit submit page with a `_autoflair=<name>` query param, this userscript opens the flair dialog and selects the matching radio option.  It deliberately does NOT click the dialog's "Add" button — Reddit's Lit-based form components ignore synthetic clicks for that final commit, so study-bot's Python side issues a real OS-level mouse click after this runs.
// @include      *://*.reddit.com/r/*/submit*
// @include      *://www.reddit.com/r/*/submit*
// @include      *://sh.reddit.com/r/*/submit*
// @include      *://new.reddit.com/r/*/submit*
// @match        *://*.reddit.com/*
// @run-at       document-end
// @grant        none
// ==/UserScript==

(async () => {
    'use strict';

    const log = (...args) => console.log('[study-bot auto-flair]', ...args);
    const warn = (...args) => console.warn('[study-bot auto-flair]', ...args);

    log('userscript loaded; href =', location.href);

    if (!/\/submit/.test(location.pathname)) {
        log('not a submit page; doing nothing');
        return;
    }

    const flairName = new URLSearchParams(location.search).get('_autoflair');
    if (!flairName) {
        log('no _autoflair query param; doing nothing');
        return;
    }
    log('will try to apply flair:', flairName);

    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

    // Walk the document tree including any open shadow roots, yielding every
    // element matching the selector.  Reddit's `shreddit` components use
    // shadow DOM heavily, so a plain document.querySelectorAll is not enough.
    function* deepQueryAll(selector, root = document) {
        if (root.querySelectorAll) {
            for (const el of root.querySelectorAll(selector)) yield el;
        }
        const all = root.querySelectorAll ? root.querySelectorAll('*') : [];
        for (const el of all) {
            if (el.shadowRoot) yield* deepQueryAll(selector, el.shadowRoot);
        }
    }

    function visibleTextOf(el) {
        // Prefer aria-label and direct innerText so we don't accidentally
        // pick up text from deeply nested children of unrelated buttons.
        const al = el.getAttribute && el.getAttribute('aria-label');
        if (al) return al.trim();
        const it = el.innerText || el.textContent || '';
        return it.trim();
    }

    function debugSnippet(el) {
        if (!el) return '<null>';
        const html = (el.outerHTML || '').replace(/\s+/g, ' ').slice(0, 220);
        return `<${el.tagName.toLowerCase()}${el.id ? ' #' + el.id : ''} aria-label="${el.getAttribute('aria-label') || ''}"> ${html}`;
    }

    // Strict matcher: only true buttons / role=button / links / labels.
    // Excludes <span>, which was the source of false matches in v0.2.
    const CLICKABLE = 'button, [role="button"], a[role="button"], label[for], faceplate-radio-input';

    function findExactMatch(text) {
        const wanted = text.toLowerCase();
        for (const el of deepQueryAll(CLICKABLE)) {
            if (visibleTextOf(el).toLowerCase() === wanted) return el;
        }
        return null;
    }

    function findContainsMatch(text) {
        const wanted = text.toLowerCase();
        for (const el of deepQueryAll(CLICKABLE)) {
            if (visibleTextOf(el).toLowerCase().includes(wanted)) return el;
        }
        return null;
    }

    // Dispatch a real PointerEvent + MouseEvent + click sequence so the
    // target's handlers see the full chain a genuine pointer interaction
    // would produce.  Reddit's Lit-based faceplate components (the flair
    // opener, faceplate-radio-input, etc.) listen on this chain rather
    // than the bare click handler.
    //
    // We deliberately do NOT also call el.click() afterwards — for the
    // flair opener button, a synthetic click + a follow-up el.click()
    // both trigger onOpenDialog → showModal, which throws "The element
    // already has an 'open' attribute" on the second call.  The
    // synthetic events are sufficient on their own.
    function realClick(el) {
        if (!el) return;
        const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : { left: 0, top: 0, width: 0, height: 0 };
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        const init = {
            bubbles: true, cancelable: true, composed: true, view: window,
            button: 0, buttons: 1, clientX: cx, clientY: cy,
            pointerType: 'mouse', isPrimary: true,
        };
        try { el.dispatchEvent(new PointerEvent('pointerdown', init)); } catch (e) {}
        try { el.dispatchEvent(new MouseEvent('mousedown', init)); } catch (e) {}
        try { el.dispatchEvent(new PointerEvent('pointerup', init)); } catch (e) {}
        try { el.dispatchEvent(new MouseEvent('mouseup', init)); } catch (e) {}
        try { el.dispatchEvent(new MouseEvent('click', init)); } catch (e) {}
    }

    async function waitFor(predicate, { timeout = 10000, interval = 200 } = {}) {
        const start = Date.now();
        while (Date.now() - start < timeout) {
            try {
                const result = predicate();
                if (result) return result;
            } catch (e) { /* swallow */ }
            await sleep(interval);
        }
        return null;
    }

    // ---- Step 1: find and click the "Add flair and tags" button on the submit page ----
    // The shreddit composer renders this with a stable id we can target
    // exactly: <button id="reddit-post-flair-button">.  Fall back to text
    // matching if the id is ever renamed.
    const addFlairBtn = await waitFor(() => {
        for (const el of deepQueryAll('#reddit-post-flair-button')) return el;
        // Fallbacks (in priority order)
        for (const el of deepQueryAll(CLICKABLE)) {
            const al = (el.getAttribute('aria-label') || '').toLowerCase();
            if (al === 'add flair' || al === 'flair' || al.includes('flair and tags')) return el;
        }
        return findExactMatch('Add flair and tags') ||
               findExactMatch('Add flair') ||
               findContainsMatch('Add flair');
    });

    if (!addFlairBtn) {
        warn('could not find an "Add flair and tags" button on this page');
        return;
    }
    log('clicking flair opener:', debugSnippet(addFlairBtn));
    realClick(addFlairBtn);

    // ---- Step 2: confirm a dialog/popover actually opened ----
    const dialog = await waitFor(() => {
        for (const el of deepQueryAll('[role="dialog"], [role="menu"], [role="listbox"], faceplate-dialog, faceplate-menu')) {
            const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
            if (rect && rect.width > 0 && rect.height > 0) return el;
        }
        return null;
    }, { timeout: 5000 });

    if (!dialog) {
        warn('clicked the flair opener but no dialog/menu appeared; aborting');
        return;
    }
    log('flair dialog opened:', debugSnippet(dialog));

    // ---- Step 3: inside the dialog, find and click the matching option ----
    function findOptionInDialog(text) {
        const wanted = text.toLowerCase();
        // Search dialog descendants only.
        const candidates = dialog.querySelectorAll(
            'button, [role="option"], [role="menuitem"], [role="radio"], faceplate-radio-input, label'
        );
        let containsMatch = null;
        for (const el of candidates) {
            const t = visibleTextOf(el).toLowerCase();
            if (!t) continue;
            if (t === wanted) return el;
            if (!containsMatch && t.includes(wanted)) containsMatch = el;
        }
        return containsMatch;
    }

    const option = await waitFor(() => findOptionInDialog(flairName));
    if (!option) {
        warn('flair dialog opened but no option matched:', flairName);
        // Dump candidates so we can refine the matcher.
        const candidates = dialog.querySelectorAll(
            'button, [role="option"], [role="menuitem"], [role="radio"], faceplate-radio-input, label'
        );
        log('candidate option texts in dialog:',
            Array.from(candidates).map(c => visibleTextOf(c)).filter(Boolean));
        return;
    }
    log('selecting flair option:', debugSnippet(option));
    realClick(option);
    // Lit form-associated custom elements (like faceplate-radio-input) often
    // ignore a synthetic click for purposes of form-state tracking unless we
    // also fire a change event.  We dispatch them so the radio at least
    // looks selected; the actual commit happens when study-bot's Python
    // side issues an OS-level click on the dialog's "Add" button.
    try {
        option.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
        option.dispatchEvent(new Event('input', { bubbles: true, composed: true }));
    } catch (e) {}

    // We deliberately do NOT click the dialog's "Add" / "Apply" / "Confirm"
    // button here.  Reddit's flair dialog rejects synthetic clicks for that
    // final commit (the radio's internal form state never updates from a
    // JS-fired event), so even a perfectly-targeted realClick() leaves the
    // dialog open and the flair unset.  Instead, study-bot's Python side
    // takes a screenshot, finds the bright-blue Add button shape in the
    // lower half of the screen, and clicks it via pyautogui — which fires
    // a real OS mouse event with isTrusted=true that Reddit accepts.
    log('radio option selected; leaving dialog open for OS-level Add click from study-bot');
    log('flair flow finished');
})();
