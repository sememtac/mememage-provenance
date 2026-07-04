// =====================================================================
// AGE NAMES — display-side label for the integer `age` field.
// =====================================================================
// Records carry `age` (an int) at the top level, and may carry an
// `age_name` (a display string the chain chose). The label prefers the
// record's own `age_name`; with none, it renders a neutral "Age N". No
// naming scheme is baked in here — the name travels in the record.
// =====================================================================

(function (root) {
  // Neutral label from the bare integer — for callers that only have `age`.
  function ageName(n) {
    return (typeof n === 'number' && n >= 1) ? ('Age ' + n) : '';
  }

  // Preferred: label a whole record — its own age_name, else "Age N".
  function forRecord(record) {
    if (record && record.age_name) return String(record.age_name);
    var n = record && typeof record.age === 'number' ? record.age : null;
    return ageName(n);
  }

  root.AgeNames = { name: ageName, forRecord: forRecord };
})(typeof window !== 'undefined' ? window : this);
