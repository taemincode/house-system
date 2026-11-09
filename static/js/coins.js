(() => {
  const number = value => value == null ? '—' : new Intl.NumberFormat('en-US').format(value);
  const top = document.querySelector('.site-top');
  if (top && 'ResizeObserver' in window) {
    new ResizeObserver(() => document.documentElement.style.setProperty('--site-top-height',
      matchMedia('(min-width:1000px)').matches ? `${top.offsetHeight + 16}px` : '16px')).observe(top);
  }
  function updateTimes(root, serverTime = Date.now() / 1000, minutesOnly = false) {
    root.querySelectorAll('[data-timestamp]').forEach(time => {
      const minutes = Math.max(0, Math.floor((serverTime - Number(time.dataset.timestamp)) / 60));
      time.textContent = minutes < 1 ? 'Just now' : (minutesOnly || minutes < 60) ? `${minutes} ${minutes === 1 ? 'minute' : 'minutes'} ago` :
        `${Math.floor(minutes / 60)} ${Math.floor(minutes / 60) === 1 ? 'hour' : 'hours'} ago`;
    });
  }
  function updateNewBadge(article, timestamp, serverTime) {
    let badge = article.querySelector('.coin-new-badge');
    const age = serverTime - Number(timestamp);
    if (age >= 0 && age < 600) {
      if (!badge) {
        badge = document.createElement('span');
        badge.className = 'coin-new-badge';
        badge.textContent = 'n';
        badge.setAttribute('aria-label', 'New award');
        badge.title = 'New award · less than 10 minutes ago';
        article.append(badge);
      }
    } else if (badge) badge.remove();
  }
  function entry(item, emphasize = false) {
    const article = document.createElement('article');
    article.className = `coin-entry ${item.house}`;
    article.dataset.awardId = item.id;
    const amount = document.createElement('strong');
    amount.className = 'coin-amount'; amount.textContent = `+${item.amount}`;
    const content = document.createElement('div');
    const message = document.createElement('p');
    if (emphasize && item.message_parts) {
      item.message_parts.forEach(part => {
        if (part.bold) {
          const strong = document.createElement('strong'); strong.textContent = part.text; message.append(strong);
        } else message.append(document.createTextNode(part.text));
      });
    } else message.textContent = item.message;
    const time = document.createElement('time'); time.dateTime = item.datetime;
    time.dataset.timestamp = item.timestamp; time.textContent = item.time;
    content.append(message, time); article.append(amount, content);
    return article;
  }
  let lastSummary;
  function updateSummary(summary, html) {
    if (!summary) return;
    const serialized = JSON.stringify(summary);
    const standings = document.querySelector('#standings-live');
    if (lastSummary !== serialized && !(standings && standings.contains(document.activeElement))) {
      // The fragment is rendered by our own Jinja template with escaped award-independent data.
      if (standings && html) standings.innerHTML = html;
      lastSummary = serialized;
    }
    summary.rows.forEach(row => {
      document.querySelectorAll(`[data-house-total="${row.id}"]`).forEach(el => { el.textContent = number(row.total); });
      document.querySelectorAll(`[data-house-rank="${row.id}"]`).forEach(el => {
        el.textContent = row.rank == null ? '—' : row.rank;
        if (row.rank != null) {
          const sup = document.createElement('sup'); sup.textContent = ['th', 'st', 'nd', 'rd'][row.rank] || 'th'; el.append(sup);
        }
        el.closest('a').setAttribute('aria-label', `${row.rank == null ? 'Unranked' : row.rank + ' place'} — view house standings`);
      });
    });
    document.querySelectorAll('[data-donation-total]').forEach(el => { el.textContent = `${number(summary.donation_total)} zł`; });
  }
  const daily = document.querySelector('[data-today-feed]');
  if (daily) {
    const stage = daily.querySelector('.today-stage');
    let items = JSON.parse(daily.dataset.items);
    let index = 0, timer, busy = false;
    let period = daily.dataset.period, revision = daily.dataset.revision;
    let complete = !daily.dataset.next;
    let serverOffset = Number(daily.dataset.serverTime) - Date.now() / 1000;
    const currentTime = () => Date.now() / 1000 + serverOffset;
    function updateCurrentBadge() {
      if (stage.firstElementChild && items[index]) {
        updateNewBadge(stage.firstElementChild, items[index].timestamp, currentTime());
      }
    }

    function schedule() {
      clearTimeout(timer);
      if (items.length > 1 && !document.hidden && !daily.matches(':hover') && !daily.contains(document.activeElement)) {
        timer = setTimeout(() => { index = (index + 1) % items.length; show(); }, 5000);
      }
    }
    function show() {
      stage.replaceChildren(...(items.length ? [entry(items[index], true)] : []));
      stage.hidden = !items.length;
      daily.querySelector('[data-feed-empty]').hidden = items.length > 0;
      daily.className = `today-panel${items.length ? ' ' + items[index].house : ''}`;
      if (stage.firstElementChild) {
        stage.firstElementChild.setAttribute('aria-roledescription', 'slide');
        stage.firstElementChild.setAttribute('aria-label', `Award ${index + 1} of ${items.length}`);
      }
      updateTimes(stage, currentTime(), true);
      updateCurrentBadge();
      schedule();
    }
    daily.addEventListener('mouseenter', () => clearTimeout(timer));
    daily.addEventListener('mouseleave', schedule);
    daily.addEventListener('focusin', () => clearTimeout(timer));
    daily.addEventListener('focusout', () => setTimeout(schedule, 0));

    async function refreshToday(updateBoard = true) {
      if (document.hidden || busy) return;
      busy = true;
      try {
        const url = new URL(daily.dataset.todayFeed, location.origin);
        url.searchParams.set('limit', '100');
        url.searchParams.set('summary', document.querySelector('#standings-live') ? 'board' : 'totals');
        const response = await fetch(url, {cache:'no-store'});
        if (!response.ok) throw new Error('Offline');
        const data = await response.json();
        serverOffset = data.server_time - Date.now() / 1000;
        updateCurrentBadge();
        if (updateBoard) updateSummary(data.summary, data.standings_html);
        updateTimes(stage, data.server_time, true);
        if (complete && data.revision === revision && data.period === period) return;
        // Load every page so the rotation includes the whole day, not only the first few awards.
        const updated = [...data.items];
        let cursor = data.next;
        while (cursor) {
          const pageUrl = new URL(daily.dataset.todayFeed, location.origin);
          pageUrl.searchParams.set('limit', '100'); pageUrl.searchParams.set('cursor', cursor);
          const pageResponse = await fetch(pageUrl, {cache:'no-store'});
          if (!pageResponse.ok) throw new Error('Offline');
          const page = await pageResponse.json();
          if (page.period !== data.period || page.revision !== data.revision) throw new Error('Feed changed; retry next poll');
          updated.push(...page.items); cursor = page.next;
        }
        const currentId = items[index]?.id;
        items = updated;
        index = Math.max(0, items.findIndex(item => item.id === currentId));
        period = data.period; revision = data.revision; complete = true;
        show();
      } catch (_) { /* Keep the last successful rotation and retry quietly. */ }
      finally { busy = false; }
    }
    setInterval(refreshToday, 25000);
    setInterval(updateCurrentBadge, 1000);
    document.addEventListener('visibilitychange', () => {
      schedule();
      if (!document.hidden) refreshToday();
    });
    show();
    refreshToday(false);
  }
  document.querySelectorAll('[data-feed]').forEach(panel => {
    const list = panel.querySelector('[data-feed-list]');
    const more = panel.querySelector('.feed-more');
    const newer = panel.querySelector('.new-awards');
    const scroll = panel.querySelector('.daily-scroll');
    let cursor = panel.dataset.next || null;
    let period = panel.dataset.period;
    let revision = panel.dataset.revision;
    let busy = false, pending = null, expanded = false;
    const defaultLimit = scroll ? 3 : 6;
    function apply(data) {
      list.replaceChildren(...data.items.map(item => entry(item)));
      cursor = data.next; more.hidden = !cursor;
      panel.querySelector('[data-feed-empty]').hidden = data.items.length > 0;
      panel.querySelector('[data-feed-total]').textContent = number(data.total);
      newer.hidden = true; pending = null; period = data.period; revision = data.revision; expanded = false;
      if (scroll) scroll.scrollTop = 0;
      updateTimes(panel, data.server_time);
    }
    async function refresh() {
      if (document.hidden || busy) return;
      busy = true;
      try {
        const url = new URL(panel.dataset.feed, location.origin);
        url.searchParams.set('limit', defaultLimit);
        if (scroll) url.searchParams.set('summary', document.querySelector('#standings-live') ? 'board' : 'totals');
        const response = await fetch(url, {cache:'no-store'});
        if (!response.ok) throw new Error('Offline');
        const data = await response.json();
        updateSummary(data.summary, data.standings_html);
        if (data.period !== period) apply(data); // Midnight/Monday clears the old period.
        else if (data.revision !== revision) {
          panel.querySelector('[data-feed-total]').textContent = number(data.total);
          const reading = (scroll && scroll.scrollTop > 8) || (!scroll && list.getBoundingClientRect().top < (top?.offsetHeight || 0)) || expanded || list.contains(document.activeElement);
          if (reading) {
            pending = data; newer.hidden = false; newer.textContent = 'New awards · Update list';
            // Reversals must disappear immediately, even while the scroll position is preserved.
            const ids = new Set(data.active_ids);
            list.querySelectorAll('[data-award-id]').forEach(el => { if (!ids.has(el.dataset.awardId)) el.remove(); });
            panel.querySelector('[data-feed-empty]').hidden = list.children.length > 0 || data.items.length > 0;
          } else apply(data);
        }
        updateTimes(panel, data.server_time);
      } catch (_) { /* Keep the last successful state and retry at the next poll. */ }
      finally { busy = false; }
    }
    newer.addEventListener('click', () => {
      if (pending) { apply(pending); (scroll || more).focus({preventScroll:true}); }
    });
    more.addEventListener('click', async () => {
      if (!cursor || busy) return;
      busy = true; more.disabled = true;
      try {
        const url = new URL(panel.dataset.feed, location.origin);
        url.searchParams.set('cursor', cursor); url.searchParams.set('limit', scroll ? 20 : 6);
        const response = await fetch(url, {cache:'no-store'});
        if (!response.ok) throw new Error('Offline');
        const data = await response.json();
        if (data.period !== period) apply(data);
        else {
          const ids = new Set([...list.children].map(el => el.dataset.awardId));
          list.append(...data.items.filter(item => !ids.has(item.id)).map(item => entry(item)));
          cursor = data.next; more.hidden = !cursor; expanded = true;
          updateTimes(panel, data.server_time);
        }
      } catch (_) { more.textContent = 'Try loading more'; }
      finally { busy = false; more.disabled = false; }
    });
    setInterval(refresh, 25000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
    updateTimes(panel);
  });
  document.querySelectorAll('[data-coin-confirm]').forEach(form => form.addEventListener('submit', event => {
    if (!confirm(form.dataset.coinConfirm)) event.preventDefault();
  }));
  const awardForm = document.querySelector('.award-form');
  if (awardForm) {
    const amount = awardForm.elements.amount;
    const button = awardForm.querySelector('button[type=submit]');
    const update = () => {
      const value = Number(amount.value);
      awardForm.querySelector('[data-give-amount]').textContent = `${value || 1} ${value === 1 || !value ? 'coin' : 'coins'}`;
    };
    amount.addEventListener('input', update); update();
    awardForm.addEventListener('submit', () => { button.disabled = true; button.textContent = 'Saving…'; });
    window.addEventListener('pageshow', () => {
      // Back/forward cache may restore a disabled button; the server key still prevents duplicates.
      button.disabled = false; button.replaceChildren(document.createTextNode('Give '));
      const output = document.createElement('span'); output.dataset.giveAmount = ''; button.append(output); update();
    });
  }
})();
