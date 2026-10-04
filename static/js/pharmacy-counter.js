/* Caisse hors app — panier côté navigateur, envoi JSON idempotent,
   file d'attente hors connexion synchronisée au retour du réseau. */
(function () {
  var dataEl = document.getElementById('pc-products');
  if (!dataEl) return;
  var products = JSON.parse(dataEl.textContent || '[]');
  var byId = {};
  products.forEach(function (p) { byId[p.id] = p; });

  var cart = {}; // id -> qty
  var activeCat = '';
  var $ = function (id) { return document.getElementById(id); };
  var grid = $('pc-grid'), search = $('pc-search'), cats = $('pc-cats');
  var lines = $('pc-lines'), hidden = $('pc-hidden'), empty = $('pc-cart-empty');
  var submit = $('pc-submit'), submitLabel = $('pc-submit-label');
  var discountEl = $('pc-discount'), insAmountEl = $('pc-ins-amount');
  var form = $('pc-form');

  function money(n) { return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ') + ' F'; }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; }); }
  function norm(s) { return (s || '').toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); }

  // ── Catégories ──
  var catNames = [];
  products.forEach(function (p) { if (p.category && catNames.indexOf(p.category) < 0) catNames.push(p.category); });
  catNames.sort();
  function renderCats() {
    var html = '<button type="button" class="pc-chip' + (activeCat ? '' : ' is-active') + '" data-cat="">Tout</button>';
    catNames.forEach(function (c) {
      html += '<button type="button" class="pc-chip' + (activeCat === c ? ' is-active' : '') + '" data-cat="' + esc(c) + '">' + esc(c) + '</button>';
    });
    cats.innerHTML = catNames.length ? html : '';
  }
  cats.addEventListener('click', function (e) {
    var b = e.target.closest('[data-cat]');
    if (!b) return;
    activeCat = b.getAttribute('data-cat');
    renderCats();
    renderGrid(true);
  });

  // ── Catalogue ──
  function visibleProducts() {
    var q = norm(search.value.trim());
    return products.filter(function (p) {
      return (!activeCat || p.category === activeCat) && (!q || norm(p.name).indexOf(q) >= 0);
    });
  }
  function renderGrid(animate) {
    var list = visibleProducts();
    grid.classList.toggle('pc-grid--animate', !!animate);
    $('pc-noresult').classList.toggle('hidden', list.length > 0);
    grid.innerHTML = list.slice(0, 60).map(function (p, i) {
      var left = available(p.id) - (cart[p.id] || 0);
      var low = left <= 5;
      return '<button type="button" class="pc-prod' + (left <= 0 ? ' is-out' : '') + '" data-id="' + p.id + '" style="animation-delay:' + Math.min(i, 12) * 25 + 'ms"' + (left <= 0 ? ' disabled' : '') + '>' +
        '<span class="pc-prod__name">' + esc(p.name) + '</span>' +
        '<span class="pc-prod__meta">' +
          (p.rx ? '<span class="pc-badge pc-badge--rx" title="Sur ordonnance"><span class="material-symbols-rounded">prescriptions</span>Ordo</span>' : '') +
          '<span class="pc-badge' + (low ? ' pc-badge--low' : '') + '">' + left + ' en stock</span>' +
        '</span>' +
        '<span class="pc-prod__price">' + money(p.price) + '</span>' +
        (cart[p.id] ? '<span class="pc-prod__count">' + cart[p.id] + '</span>' : '') +
        '</button>';
    }).join('');
  }
  grid.addEventListener('click', function (e) {
    var b = e.target.closest('.pc-prod');
    if (b && !b.disabled) add(+b.getAttribute('data-id'), 1, b);
  });
  search.addEventListener('input', function () { renderGrid(true); });
  search.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    var first = visibleProducts().find(function (p) { return available(p.id) - (cart[p.id] || 0) > 0; });
    if (first) { add(first.id, 1); search.select(); }
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
      e.preventDefault();
      search.focus();
    }
  });

  // ── Panier ──
  function add(id, delta, sourceEl) {
    var p = byId[id];
    if (!p) return;
    var next = Math.max(0, Math.min(available(id), (cart[id] || 0) + delta));
    if (next === 0) delete cart[id]; else cart[id] = next;
    if (sourceEl && sourceEl.animate) {
      sourceEl.animate([{ transform: 'scale(1)' }, { transform: 'scale(0.94)' }, { transform: 'scale(1)' }], { duration: 220, easing: 'ease-out' });
    }
    render(id);
  }
  lines.addEventListener('click', function (e) {
    var b = e.target.closest('[data-step]');
    if (!b) return;
    add(+b.getAttribute('data-id'), +b.getAttribute('data-step'));
  });
  lines.addEventListener('change', function (e) {
    if (!e.target.matches('.pc-line__qty')) return;
    var id = +e.target.getAttribute('data-id');
    var v = parseInt(e.target.value, 10) || 0;
    cart[id] = 0;
    add(id, v);
  });
  $('pc-clear').addEventListener('click', function () { cart = {}; render(); });

  function totals() {
    var subtotal = 0, rx = false, count = 0;
    Object.keys(cart).forEach(function (id) {
      var p = byId[id];
      subtotal += p.price * cart[id];
      count += cart[id];
      if (p.rx) rx = true;
    });
    var discount = Math.min(subtotal, Math.max(0, parseInt(discountEl.value, 10) || 0));
    var insOn = form.querySelector('input[name="payment_method"]:checked').value === 'insurance';
    var ins = insOn ? Math.min(subtotal - discount, Math.max(0, parseInt(insAmountEl.value, 10) || 0)) : 0;
    return { subtotal: subtotal, discount: discount, ins: ins, total: subtotal - discount - ins, rx: rx, count: count, insOn: insOn };
  }

  function render(changedId) {
    var ids = Object.keys(cart);
    empty.classList.toggle('hidden', ids.length > 0);
    $('pc-clear').classList.toggle('hidden', ids.length === 0);
    lines.innerHTML = ids.map(function (id) {
      var p = byId[id];
      return '<li class="pc-line' + (String(changedId) === id ? ' gp-pop' : '') + '">' +
        '<div class="min-w-0 flex-1"><p class="pc-line__name">' + esc(p.name) + (p.rx ? ' <span class="pc-badge pc-badge--rx">Ordo</span>' : '') + '</p>' +
        '<p class="pc-line__unit">' + money(p.price) + ' / u.</p></div>' +
        '<div class="pc-stepper">' +
          '<button type="button" data-step="-1" data-id="' + id + '" aria-label="Retirer un"><span class="material-symbols-rounded sm">remove</span></button>' +
          '<input class="pc-line__qty" type="number" min="0" max="' + available(id) + '" value="' + cart[id] + '" data-id="' + id + '" aria-label="Quantité">' +
          '<button type="button" data-step="1" data-id="' + id + '" aria-label="Ajouter un"><span class="material-symbols-rounded sm">add</span></button>' +
        '</div>' +
        '<p class="pc-line__total">' + money(p.price * cart[id]) + '</p>' +
        '</li>';
    }).join('');
    hidden.innerHTML = ids.map(function (id) {
      return '<input type="hidden" name="stock_id" value="' + id + '"><input type="hidden" name="quantity" value="' + cart[id] + '">';
    }).join('');
    refreshTotals();
    renderGrid(false);
  }

  function refreshTotals() {
    var t = totals();
    $('pc-subtotal').textContent = money(t.subtotal);
    $('pc-sum-discount').textContent = '− ' + money(t.discount);
    $('pc-row-discount').classList.toggle('hidden', !t.discount);
    $('pc-sum-ins').textContent = '− ' + money(t.ins);
    $('pc-row-ins').classList.toggle('hidden', !t.ins);
    $('pc-total').textContent = money(t.total);
    $('pc-insurance').classList.toggle('hidden', !t.insOn);
    $('pc-rx').classList.toggle('hidden', !t.rx);
    var rxOk = !t.rx || $('pc-rx-check').checked;
    submit.disabled = !t.count || !rxOk;
    submitLabel.textContent = t.count ? 'Encaisser ' + money(t.total) + ' · ' + t.count + ' article' + (t.count > 1 ? 's' : '') : 'Encaisser';
  }

  // Tiers payant : pré-remplit la part assurance avec le taux de l'organisme
  form.addEventListener('change', function (e) {
    if (e.target.name === 'insurance_provider') {
      var opt = e.target.selectedOptions[0];
      var rate = opt ? parseInt(opt.getAttribute('data-rate'), 10) || 0 : 0;
      var t = totals();
      insAmountEl.value = Math.round((t.subtotal - t.discount) * rate / 100);
    }
    if (e.target.name === 'payment_method' && (e.target.value === 'credit' || e.target.value === 'insurance')) {
      $('pc-customer').open = true;
    }
    refreshTotals();
  });
  form.addEventListener('input', function (e) {
    if (e.target === discountEl || e.target === insAmountEl) refreshTotals();
  });
  // ══ Envoi & hors connexion ══════════════════════════════════════
  var API = form.getAttribute('data-api');
  var QKEY = 'gp_caisse_queue_' + form.getAttribute('data-pharmacy');
  var syncing = false;

  function uuid4() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0;
      return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }
  function loadQueue() { try { return JSON.parse(localStorage.getItem(QKEY) || '[]'); } catch (e) { return []; } }
  function saveQueue(q) { try { localStorage.setItem(QKEY, JSON.stringify(q)); } catch (e) {} }
  // Stock affiché = stock serveur − ventes encore en attente sur cet appareil
  function available(id) {
    var p = byId[id];
    if (!p) return 0;
    var pending = 0;
    loadQueue().forEach(function (sale) {
      sale.lines.forEach(function (l) { if (String(l.stock_id) === String(id)) pending += l.quantity; });
    });
    return Math.max(0, p.qty - pending);
  }
  function csrf() {
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    var i = form.querySelector('input[name="csrfmiddlewaretoken"]');
    return i ? i.value : '';
  }
  function field(name) {
    var el = form.querySelector('[name="' + name + '"]:checked') || form.querySelector('[name="' + name + '"]');
    return el ? el.value : '';
  }
  function buildPayload() {
    return {
      uuid: uuid4(),
      created_at: new Date().toISOString(),
      lines: Object.keys(cart).map(function (id) { return { stock_id: +id, quantity: cart[id] }; }),
      channel: field('channel'),
      payment_method: field('payment_method'),
      customer_name: field('customer_name'),
      customer_phone: field('customer_phone'),
      discount: parseInt(field('discount'), 10) || 0,
      insurance_provider: field('insurance_provider'),
      insurance_amount: parseInt(field('insurance_amount'), 10) || 0,
      insured_number: field('insured_number'),
      prescription_checked: $('pc-rx-check').checked,
      notes: field('notes'),
      // Données d'affichage local (ticket hors connexion), non envoyées
      _items: Object.keys(cart).map(function (id) { return { name: byId[id].name, qty: cart[id], price: byId[id].price }; }),
      _totals: totals()
    };
  }
  function post(payload) {
    var body = {};
    Object.keys(payload).forEach(function (k) { if (k.charAt(0) !== '_') body[k] = payload[k]; });
    var ctrl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, 10000);
    return fetch(API, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify(body),
      signal: ctrl ? ctrl.signal : undefined
    }).then(function (res) {
      clearTimeout(timer);
      return res.json()
        .catch(function () { return { ok: false, error: 'Réponse serveur illisible.' }; })
        .then(function (data) { data._status = res.status; return data; });
    }, function (err) { clearTimeout(timer); throw err; });
  }

  // ── Bandeau d'état ──
  var statusEl = $('pc-netstatus'), pendingEl = $('pc-pending'), syncBtn = $('pc-sync');
  function refreshStatus(msg) {
    var q = loadQueue();
    var online = navigator.onLine;
    statusEl.className = 'pc-net ' + (online ? 'pc-net--on' : 'pc-net--off');
    statusEl.querySelector('[data-label]').textContent =
      msg || (online ? 'En ligne' : 'Hors connexion — les ventes sont gardées sur cet appareil');
    pendingEl.textContent = q.length ? q.length + ' vente' + (q.length > 1 ? 's' : '') + ' à synchroniser' : '';
    pendingEl.classList.toggle('hidden', !q.length);
    syncBtn.classList.toggle('hidden', !q.length || !online);
  }
  function toast(msg, kind) {
    var t = $('pc-flash');
    t.textContent = msg;
    t.className = 'pc-flash pc-flash--' + (kind || 'info') + ' gp-pop';
    clearTimeout(t._h);
    t._h = setTimeout(function () { t.className = 'pc-flash hidden'; }, 6000);
  }

  function syncQueue() {
    if (syncing || !navigator.onLine) return Promise.resolve();
    var q = loadQueue();
    if (!q.length) return Promise.resolve();
    syncing = true;
    refreshStatus('Synchronisation…');
    var done = 0, failed = [], warnings = [];
    return q.reduce(function (chain, sale) {
      return chain.then(function () {
        return post(Object.assign({}, sale, { offline: true })).then(function (data) {
          if (data.ok) {
            done++;
            if (data.warning) warnings.push(data.code + ' : ' + data.warning);
          } else if (data._status >= 400 && data._status < 500 && data._status !== 403 && data._status !== 401) {
            sale._error = data.error;  // refusée : gardée pour que l'équipe la corrige
            failed.push(sale);
          } else {
            failed.push(sale);
          }
        }, function () { failed.push(sale); });
      });
    }, Promise.resolve()).then(function () {
      saveQueue(failed);
      syncing = false;
      refreshStatus();
      renderGrid(false);
      var rejected = failed.filter(function (s) { return s._error; });
      if (rejected.length) toast('Vente refusée à la synchro : ' + rejected[0]._error, 'error');
      else if (warnings.length) toast(warnings[0], 'warning');
      else if (done) toast(done + ' vente' + (done > 1 ? 's' : '') + ' hors connexion synchronisée' + (done > 1 ? 's' : '') + '.', 'success');
    });
  }

  function printLocalTicket(sale) {
    var w = window.open('', '_blank', 'width=360,height=640');
    if (!w) return;
    var rows = sale._items.map(function (i) {
      return '<tr><td colspan="2">' + esc(i.name) + '</td></tr><tr><td>' + i.qty + ' × ' + money(i.price) +
        '</td><td style="text-align:right">' + money(i.qty * i.price) + '</td></tr>';
    }).join('');
    w.document.write(
      '<!doctype html><meta charset="utf-8"><title>Ticket</title>' +
      '<body style="font-family:monospace;width:76mm;margin:auto">' +
      '<h3 style="text-align:center">' + esc(form.getAttribute('data-pharmacy-name') || '') + '</h3>' +
      '<p>' + new Date(sale.created_at).toLocaleString('fr-FR') +
      '<br>Vente hors connexion — réf. provisoire ' + sale.uuid.slice(0, 8).toUpperCase() + '</p><hr>' +
      '<table style="width:100%;font-size:12px">' + rows + '</table><hr>' +
      '<p><b>Payé : ' + money(sale._totals.total) + '</b></p>' +
      '<p style="text-align:center">Merci de votre visite.</p></body>'
    );
    w.document.close();
    w.focus();
    w.print();
  }

  function resetCart() {
    cart = {};
    $('pc-rx-check').checked = false;
    ['customer_name', 'customer_phone', 'notes', 'insured_number'].forEach(function (n) {
      var el = form.querySelector('[name="' + n + '"]');
      if (el) el.value = '';
    });
    discountEl.value = 0;
    insAmountEl.value = 0;
    render();
  }

  function queueOffline(payload) {
    var q = loadQueue();
    q.push(payload);
    saveQueue(q);
    refreshStatus();
    var box = $('pc-offline-done');
    box.querySelector('[data-total]').textContent = money(payload._totals.total);
    box.classList.remove('hidden');
    box.querySelector('[data-print]').onclick = function () { printLocalTicket(payload); };
    resetCart();
  }

  form.addEventListener('submit', function (e) {
    if (!window.fetch) return; // repli : envoi classique du formulaire
    e.preventDefault();
    if (!totals().count) return;
    var payload = buildPayload();
    submit.disabled = true;
    submitLabel.textContent = 'Enregistrement…';
    $('pc-offline-done').classList.add('hidden');

    if (!navigator.onLine) { queueOffline(payload); return; }
    post(payload).then(function (data) {
      if (data.ok) { window.location.href = data.done_url; return; }
      if (data._status >= 500) {
        queueOffline(payload);
        toast('Serveur indisponible : vente gardée sur cet appareil.', 'warning');
        return;
      }
      toast(data.error || 'Vente refusée.', 'error');
      refreshTotals();
    }, function () {
      // Réseau coupé pendant l'envoi : la vente est gardée et renvoyée plus tard
      // avec le même identifiant (aucun doublon si elle était déjà arrivée).
      queueOffline(payload);
    });
  });

  syncBtn.addEventListener('click', syncQueue);
  window.addEventListener('online', function () { refreshStatus(); syncQueue(); });
  window.addEventListener('offline', function () { refreshStatus(); });
  setInterval(syncQueue, 30000);

  if ('serviceWorker' in navigator && form.getAttribute('data-sw')) {
    navigator.serviceWorker
      .register(form.getAttribute('data-sw'), { scope: form.getAttribute('data-scope') })
      .catch(function () {});
  }
  refreshStatus();
  syncQueue();

  renderCats();
  render();
  renderGrid(true);
  if (window.matchMedia('(min-width: 1024px)').matches) search.focus();
})();
