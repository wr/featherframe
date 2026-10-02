// Ages on the page, as the server's _ago writes them (W-946): a copy of the
// page served later from Featherframe Cloud's cache still says the right
// age. Held to server/tests/fixtures/page-time-cases.json with _ago.
function ffAgo(ts, now) {
  var secs = Math.max(0, now - ts);
  if (secs < 60) return 'just now';
  var mins = Math.floor(secs / 60);
  if (mins < 60) return mins + ' min ago';
  var hours = Math.floor(mins / 60);
  if (hours < 24) return hours === 1 ? '1 hour ago' : hours + ' hours ago';
  var days = Math.floor(hours / 24);
  return days === 1 ? 'yesterday' : days + ' days ago';
}
