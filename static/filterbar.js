/* Filter bar chips: shows what is currently applied and lets you remove one filter at a time.
   Built from the form's own state at page load (which the server filled in from the applied
   query string), so it always matches what the results show. Markers on the controls:
     input[data-chip="Company"]            text / date boxes with a value
     input[type=checkbox][data-chip="Type"] every ticked box (label: data-label, next label/span, or value)
     select[data-chip][data-chip-default]   a select that is not at its default
   Clicking a chip clears that one control and re-submits the form. */
(function () {
    var bar = document.querySelector('.fbar');
    if (!bar) return;
    var form = bar.querySelector('form.fbar-form');
    var holder = bar.querySelector('.fbar-chips');
    if (!form || !holder) return;

    var chips = [];
    form.querySelectorAll('input[data-chip]:not([type=checkbox])').forEach(function (el) {
        var v = el.value.trim();
        if (v) chips.push({ text: el.dataset.chip + ': ' + v, clear: function () { el.value = ''; } });
    });
    form.querySelectorAll('input[type=checkbox][data-chip]:checked').forEach(function (el) {
        var sib = el.nextElementSibling;
        var name = el.dataset.label || (sib && sib.textContent.trim()) || el.value;
        chips.push({ text: el.dataset.chip + ': ' + name, clear: function () { el.checked = false; } });
    });
    form.querySelectorAll('select[data-chip]').forEach(function (el) {
        var def = el.dataset.chipDefault || '';
        if (el.value !== def) {
            chips.push({
                text: el.dataset.chip + ': ' + el.options[el.selectedIndex].textContent.trim(),
                clear: function () { el.value = def; }
            });
        }
    });
    if (!chips.length) return;

    chips.forEach(function (c) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'fbar-chip';
        b.title = 'Remove this filter';
        var t = document.createElement('span'); t.textContent = c.text;
        var x = document.createElement('span'); x.className = 'fbar-chip-x'; x.innerHTML = '&times;';
        b.appendChild(t); b.appendChild(x);
        b.addEventListener('click', function () {
            c.clear();
            if (form.requestSubmit) form.requestSubmit(); else form.submit();
        });
        holder.appendChild(b);
    });
    holder.hidden = false;
})();
