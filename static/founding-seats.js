/* Live founding-seats counter. Replaces the static "500 SEATS" badge
 * on the Founding Lifetime pricing card with the real "X seats left"
 * count from /api/account/founding-seats. Falls back silently to the
 * static badge on any failure — never flashes a broken state.
 *
 * Loaded on /pricing and / (landing). Pure progressive enhancement.
 */
(function () {
  var badge = document.querySelector('.pricing-badge--accent');
  if (!badge) return;
  var orig = badge.textContent;

  fetch('/api/account/founding-seats', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || !d.ok) return;
      var left = (typeof d.seats_remaining === 'number') ? d.seats_remaining : null;
      var total = d.seats_total || 500;
      if (left === null || left < 0) return;
      if (left <= 0) {
        badge.textContent = 'SOLD OUT';
        return;
      }
      // Tighter, urgent copy when stock is low.
      if (left <= 25) {
        badge.textContent = left + ' SEAT' + (left === 1 ? '' : 'S') + ' LEFT';
      } else if (left < total) {
        badge.textContent = left + ' / ' + total + ' SEATS LEFT';
      } else {
        // No takers yet — keep the static 'launch only' badge as-is.
        badge.textContent = orig;
      }
    })
    .catch(function () { /* leave the static badge */ });
})();
