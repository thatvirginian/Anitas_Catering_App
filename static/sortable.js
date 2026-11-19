/* Click-to-sort for tables marked data-sortable.
   Click a header: ascending -> descending -> back to the original (server) order.
   th[data-sort="text|num"] makes a column sortable; a cell's data-v overrides its visible text
   (used for ISO dates, order numbers, file counts). Sorting only reorders rows already on the page. */
(function () {
    document.querySelectorAll('table[data-sortable]').forEach(function (table) {
        var body = table.tBodies[0];
        if (!body) return;
        var rows = Array.prototype.slice.call(body.rows);
        rows.forEach(function (r, i) { r._i = i; });
        var heads = Array.prototype.slice.call(table.tHead.rows[0].cells);
        var state = { col: -1, dir: 0 };            // dir: 1 asc, -1 desc, 0 original

        function val(row, c, num) {
            var cell = row.cells[c];
            var v = cell.dataset.v !== undefined ? cell.dataset.v : cell.textContent.trim();
            if (num) { var n = parseFloat(v); return isNaN(n) ? null : n; }
            return v.toLowerCase();
        }
        function paint() {
            heads.forEach(function (h, i) {
                if (!h.hasAttribute('data-sort')) return;
                var d = i === state.col ? state.dir : 0;
                h.setAttribute('aria-sort', d === 1 ? 'ascending' : d === -1 ? 'descending' : 'none');
                var ind = h.querySelector('.sort-ind');
                if (ind) ind.textContent = d === 1 ? '▲' : d === -1 ? '▼' : '⇅';
            });
        }
        function apply() {
            var c = state.col, dir = state.dir;
            var num = c >= 0 && heads[c].getAttribute('data-sort') === 'num';
            var sorted = rows.slice().sort(function (a, b) {
                if (dir === 0) return a._i - b._i;
                var x = val(a, c, num), y = val(b, c, num);
                if (x === null && y === null) return a._i - b._i;
                if (x === null) return 1;            // blanks always last
                if (y === null) return -1;
                if (x < y) return -dir;
                if (x > y) return dir;
                return a._i - b._i;
            });
            sorted.forEach(function (r) { body.appendChild(r); });
            paint();
        }
        heads.forEach(function (h, i) {
            if (!h.hasAttribute('data-sort')) return;
            h.classList.add('sortable');
            h.tabIndex = 0;
            h.insertAdjacentHTML('beforeend', ' <span class="sort-ind" aria-hidden="true">⇅</span>');
            function go() {
                if (state.col !== i) { state.col = i; state.dir = 1; }
                else if (state.dir === 1) state.dir = -1;
                else { state.dir = 0; state.col = -1; }
                apply();
            }
            h.addEventListener('click', go);
            h.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); } });
        });
    });
})();
