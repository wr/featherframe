// How old the status the page shows is (W-950, W-954). Every /api/status
// answer has a time: when Featherframe Cloud's front door filled its copy
// (X-FF-Copy-At), else the answer's own Date. An answer older than the one
// the page shows is not applied: a live answer (?live=1, while a firmware
// update runs) followed by the front door's older copy would roll the page
// back, and bring back an age it no longer has.
// The header's note: past STALE the age, with Update now; Updating… once that
// is pressed, until a current answer reloads the page or UPDATE_WAIT passes;
// "Updated just now" for CONFIRM when an old page becomes current on its own.
// Held to hosted/test/pageage.test.ts.
function ffPageAge() {
  var STALE = 5 * 60000, UPDATE_WAIT = 120000, CONFIRM = 5000;
  var shown = 0, updating = 0, aged = false, confirmed = 0;
  function isUpdating(now) {
    if (updating && now - updating > UPDATE_WAIT) updating = 0;
    return !!updating;
  }
  return {
    CONFIRM: CONFIRM,
    // An answer, from its headers: true when the page is to apply it.
    answer: function (copyAt, date, now) {
      var at = Number(copyAt) || Date.parse(date || '') || now;
      if (at < shown) return false;
      shown = at;
      if (aged && now - at <= STALE && !isUpdating(now)) confirmed = now;
      return true;
    },
    update: function (now) { updating = now; },
    isUpdating: isUpdating,
    // After Update now, a current answer: the page reloads.
    reload: function (now) { return isUpdating(now) && now - shown <= STALE; },
    // What the note says, or null when it is hidden.
    note: function (now) {
      if (isUpdating(now)) return { text: 'Updating…', act: false };
      if (shown && now - shown > STALE) {
        aged = true;
        return { text: 'Updated ' + ffAgo(shown / 1000, now / 1000), act: true };
      }
      if (confirmed && now - confirmed < CONFIRM) return { text: 'Updated just now', act: false };
      aged = false;
      confirmed = 0;
      return null;
    }
  };
}
