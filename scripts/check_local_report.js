#!/usr/bin/env node
/**
 * Exercise the report's real functions with no browser, and assert the things
 * that have actually broken.
 *
 * Every check below exists because that exact failure shipped at least once and
 * was found by eye rather than by a test. None of them were caught by counting
 * roles, which is why counting roles is not enough: the numbers stayed
 * plausible while priority, employment type and department were quietly broken
 * across an entire half-year.
 *
 *   node scripts/check_local_report.js              # invariant suite + summary
 *   node scripts/check_local_report.js 2026-07      # one month, with detail
 *   node scripts/check_local_report.js REC-245      # trace one ticket
 *
 * Point it at another file with REPORT_LOCAL_OUT — the public mirror, say,
 * whose password-gate script sits ahead of the report's own.
 */
const fs = require('fs');
const path = require('path');

const OUT = process.env.REPORT_LOCAL_OUT ||
  path.join(process.env.HOME, 'Documents', 'Reports', 'report_local.html');

if (!fs.existsSync(OUT)) {
  console.error(`not found: ${OUT}\nrun: python3 scripts/build_local_report.py`);
  process.exit(1);
}

// ── minimal DOM so the report's bootstrap can run ──
const store = {}, noop = () => {};
// Segmented controls are real enough to answer "is this button on / disabled".
// The period bar's two segs drive the Month|Episode toggle and the freeze, and
// both were asserted by hand in a throwaway harness before landing here.
function segButton(attrs) {
  return {
    getAttribute: k => (k in attrs ? attrs[k] : null),
    classList: { toggle: (c, on) => { if (c === 'on') attrs._on = on; },
                 add: noop, remove: noop, contains: () => !!attrs._on },
    get on() { return !!attrs._on; },
    set disabled(v) { attrs._dis = v; }, get disabled() { return !!attrs._dis; },
  };
}
const SEGS = {
  'f-overview-grain': [segButton({ 'data-g': 'month' }), segButton({ 'data-g': 'episode' })],
  'f-overview-period': [segButton({ 'data-p': 'month' })],
};
function el(id) {
  if (SEGS[id]) return { querySelectorAll: () => SEGS[id], style: {},
                         classList: { toggle: noop, add: noop, remove: noop } };
  return new Proxy({ _id: id }, {
    get: (t, p) => {
      if (p === 'style') return {};
      if (p === 'classList') return { add: noop, remove: noop, toggle: noop, contains: () => false };
      if (p === 'value') return store[t._id + '#value'] || '';
      if (p === 'querySelectorAll') return () => [];
      if (p === 'querySelector') return () => null;
      if (p === 'closest') return () => el('_closest');
      if (p === 'getBoundingClientRect') return () => ({ height: 0 });
      if (p === 'appendChild') return noop;
      if (p === 'options') return [];
      if (p === 'innerHTML' || p === 'textContent') return store[t._id] || '';
      return '';
    },
    set: (t, p, v) => {
      if (p === 'value') store[t._id + '#value'] = v;
      else if (p === 'innerHTML' || p === 'textContent') store[t._id] = v;
      return true;
    },
  });
}
global.document = {
  getElementById: el, querySelectorAll: () => [], querySelector: () => null,
  createElement: () => el('_tmp'), body: el('body'), addEventListener: noop,
};
global.window = { innerWidth: 1400, addEventListener: noop };
global.innerWidth = 1400; global.innerHeight = 900;
global.sessionStorage = { getItem: () => '1', setItem: noop };
// The report reads the URL fragment on boot to restore a frozen period, and
// writes it back when you freeze one.
let HASH = '';
global.location = { get hash() { return HASH; }, set hash(v) { HASH = v; },
                    origin: 'https://example.invalid', pathname: '/report.html', search: '' };
global.history = { replaceState: (a, b, url) => {
  HASH = url && url.indexOf('#') >= 0 ? url.slice(url.indexOf('#')) : '';
} };
global.navigator = {};

const html = fs.readFileSync(OUT, 'utf8');

// The mirror carries a password-gate <script> ahead of the report's own, so take
// the block that actually declares the data rather than simply the first one.
function reportScript(src) {
  const blocks = [...src.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  const found = blocks.filter(b => b.includes('const OP='));
  if (found.length !== 1) {
    console.error(`expected exactly one script block declaring OP, found ${found.length}`);
    process.exit(1);
  }
  return found[0];
}

eval(reportScript(html) + `
;global.__R={activeItems,closedItems,stoppedItems,setMonth,deptOf,prBucket,esBucket,
             _episodeAt,_episodeIndex,_episodeLabel,closedItemsEpisode,_hsBase,_allCards,
             _roleKey,activeOpenings,senOf,_episodeHasFullData,
             renderOverview,renderActive,renderClosed,toggleDept,itemsFor,DEPT_SPLITS,subBucket,
             setGrain,setEpisode,onPeriodPick,_finishedEpisodes,_periodHash,_readPeriodHash,
             toggleLock,PERIOD,GRAIN:()=>GRAIN,locked:()=>PERIOD_LOCKED,
             csRaw,csLabel,csCell,_esc,renderClosedDetail:renderClosed};`);
const R = global.__R;

const hc = rows => rows.reduce((s, v) => s + (v.h || 1), 0);
const end = v => v.cxd || v.fcd || v.hd || v.rd || null;
const arg = process.argv[2];

// ── single-ticket trace ──
if (arg && /^[A-Z]+-\d+/.test(arg)) {
  const card = R._allCards().find(v => v.key === arg);
  if (!card) { console.error(`${arg} not in the dataset`); process.exit(1); }
  console.log(`${arg}  ${card.s}`);
  console.log(`  status=${card.st}  opened=${card.sde || card.sd}  end=${end(card) || '—'}` +
              `  parent=${card.pk || '—'}  dept=${card.t || '—'}  h=${card.h}`);
  console.log('\n  active in:');
  for (let m = 1; m <= 12; m++) {
    const mm = `2026-${String(m).padStart(2, '0')}`;
    R.setMonth('active', mm);
    const hit = R.activeItems('active').find(v => v.key === arg || v.key === card.pk);
    if (hit) console.log(`    ${mm}  as role ${hit.key}, h=${hit.h}`);
  }
  process.exit(0);
}

// ── one month in detail ──
if (arg) {
  R.setMonth('active', arg);
  const rows = R.activeItems('active');
  console.log(`${arg}:  ${rows.length} roles, ${hc(rows)} headcount, ` +
              `${R.closedItems('closed').length} closed, ${R.stoppedItems('active').length} stopped\n`);
  rows.slice().sort((a, b) => R.deptOf(a).localeCompare(R.deptOf(b)) || (a.s || '').localeCompare(b.s || ''))
    .forEach(v => console.log(
      `  ${R.deptOf(v).slice(0, 20).padEnd(21)} ${(v.s || '').slice(0, 38).padEnd(39)} ` +
      `${String(v.sn || '—').padEnd(7)} h=${String(v.h).padStart(2)}  ` +
      `${(v.sde || v.sd || '—').padEnd(11)} ${end(v) ? v.st + ' ' + end(v) : ''}`));
  process.exit(0);
}

// ── summary ──
console.log('month     roles  headcount  closed  stopped');
for (let m = 1; m <= 12; m++) {
  const mm = `2026-${String(m).padStart(2, '0')}`;
  R.setMonth('active', mm);
  const rows = R.activeItems('active'), closed = R.closedItems('closed');
  if (!rows.length && !closed.length) continue;
  console.log(`${mm}    ${String(rows.length).padStart(4)}   ${String(hc(rows)).padStart(6)}` +
              `     ${String(closed.length).padStart(4)}     ${String(R.stoppedItems('active').length).padStart(4)}`);
}

// ── invariants ──
let failed = 0;
function check(label, bad, describe = v => v.key) {
  const list = bad || [];
  if (list.length) failed++;
  const detail = list.length
    ? ' → ' + list.slice(0, 6).map(describe).join(', ') +
      (list.length > 6 ? ` (+${list.length - 6})` : '')
    : '';
  console.log(`  ${list.length ? '✗' : '✓'} ${label}${detail}`);
}
const fact = (label, ok, got) => check(label, ok ? [] : [{ key: got }]);

console.log('\ninvariants:');
const all = R._allCards();
const byKey = {}; all.forEach(v => { byKey[v.key] = v; });

// Period scoping — the original three, still the core of the report.
R.setMonth('active', '2026-07');
const jul = R.activeItems('active');
check('no role filled inside the month stays active',
      jul.filter(v => { const f = v.fcd || v.hd || v.rd; return f && f <= '2026-07-31' && !v.cxd; }));
check('active and closed are disjoint',
      R.closedItems('closed').filter(c => jul.some(a => a.key === c.key)));
check('every active role has a start date', jul.filter(v => !(v.sde || v.sd)));

// Headcount is a card count. Jira copies num_hires onto sub-tasks instead of
// setting 1, so a card carrying anything else re-opens the inflation that had
// Hiring Sources reporting 12 hires against five closures.
check('every card counts as exactly one opening (h === 1)',
      all.filter(v => v.h !== 1), v => `${v.key}(h=${v.h})`);
R.setMonth('active', '2026-08');
const hs = R._hsBase();
fact('hiring-sources total equals its row count', hc(hs) === hs.length, `${hc(hs)} vs ${hs.length}`);

// A role is department + vacancy + seniority, so no two rows may share that key,
// and seniority must never be blank on a role — it falls back to the parent
// precisely so a blank does not split one role in two.
R.setMonth('active', '2026-03');
const roles = R.activeItems('active');
const keys = roles.map(R._roleKey);
check('no two roles share department, vacancy and seniority',
      keys.filter((k, i) => keys.indexOf(k) !== i).map(k => ({ key: k })));
fact('the role table and the openings agree on headcount',
     roles.reduce((s, v) => s + v.h, 0) === R.activeOpenings('active').length,
     `${roles.reduce((s, v) => s + v.h, 0)} vs ${R.activeOpenings('active').length}`);
check('a role opens on its earliest opening',
      roles.filter(r => R.activeOpenings('active')
        .filter(o => R._roleKey(o) === R._roleKey(r))
        .some(o => (o.sde || o.sd) < (r.sde || r.sd))));

// Structure. An orphaned pk silently promotes a sub-task to a role of its own;
// a two-level chain splits one requisition back into two, because _rolesFrom
// resolves a single level of parent.
check('no sub-task points at a missing parent',
      all.filter(v => v.pk && !byKey[v.pk]), v => `${v.key}→${v.pk}`);
check('no two-level parent chains',
      all.filter(v => v.pk && byKey[v.pk] && byKey[v.pk].pk),
      v => `${v.key}→${v.pk}→${byKey[v.pk].pk}`);
check('no card is its own parent', all.filter(v => v.pk === v.key));

// Taxonomy. The spreadsheets spell their categories differently from Jira and
// the render layer compares verbatim, so an un-normalised value does not raise
// — it lands in the fallback bucket. That shipped for a whole half-year: 178
// rows shown as "Not specified" priority, 153 as employment type "n/a".
const sheet = all.filter(v => v.origin === 'sheet');
fact('spreadsheet rows are present at all', sheet.length > 0, 'none found');
check('spreadsheet rows resolve to a real priority bucket',
      sheet.filter(v => R.prBucket(v) === '—'), v => `${v.key}(${v.pr})`);
check('spreadsheet rows resolve to a real employment bucket',
      sheet.filter(v => R.esBucket(v) === 'n/a'), v => `${v.key}(${v.es})`);
// The failure to catch is one source ABBREVIATING a department the other spells
// out — 'E-com' against 'E-commerce' showed as two rows on Overview. Comparing
// on a shared prefix of fixed length is too blunt: it condemns Brand Design
// beside Brand Communications, and Product Design (Web) beside Product (Web),
// which are genuinely separate departments. One name being a strict prefix of
// the other, once case and punctuation are gone, catches abbreviations only.
const deptsFrom = isSheet => new Set(all.filter(v => (v.origin === 'sheet') === isSheet).map(R.deptOf));
const flat = s => s.toLowerCase().replace(/[^a-z0-9]/g, '');
const jiraDepts = [...deptsFrom(false)];
check('no department is spelled out by one source and abbreviated by the other',
      [...deptsFrom(true)].filter(d => !jiraDepts.includes(d) && jiraDepts.some(j => {
        const [a, b] = [flat(d), flat(j)];
        return a !== b && (a.startsWith(b) || b.startsWith(a));
      })), d => d);

// A team name that already states its board keeps that BOARD. A WRP card whose
// Team Web reads "Product (Mobile)" — which is how a web-board card is assigned
// to the mobile side — must stay on the mobile side rather than being forced to
// "(Web)" by its project.
//
// This used to demand the whole name survive verbatim. That was too strong once
// Design became its own department: XL-117 is filed under "Product (Mobile)"
// with Subteam=Design and is now "Product Design (Mobile)" — renamed, same
// board. The board is the part that must not move.
const boardOf = s => (/\((Mobile|Web)\)$/.exec(s || '') || [])[1] || null;
check('a team that names its own board keeps that board',
      all.filter(v => boardOf(v.t) && boardOf(R.deptOf(v)) !== boardOf(v.t)),
      v => `${v.key}: ${v.t} → ${R.deptOf(v)}`);

// ── Design is a department, not a subteam of Product ──
// The promotion keys off Subteam, so it is exactly as complete as that column;
// these pin both directions of the rule.
check('no Product card tagged Design is still filed under Product',
      all.filter(v => /^Product( \(Mobile\))?$/.test(v.t || '') && /^Design/i.test(v.sb || '')
                      && !/^Product Design/.test(R.deptOf(v))),
      v => `${v.key} sb=${JSON.stringify(v.sb)} → ${R.deptOf(v)}`);
check('a team literally named Design becomes Product Design',
      all.filter(v => (v.t || '') === 'Design' && !/^Product Design \((Mobile|Web)\)$/.test(R.deptOf(v))),
      v => `${v.key} → ${R.deptOf(v)}`);
// Decided deliberately: the promotion is mobile-only, and UX Research is a
// different discipline. Both would be silent if they regressed.
check('the promotion does not reach the web Product board',
      all.filter(v => (v.t || '') === 'Product (Web)' && /^Product Design/.test(R.deptOf(v))),
      v => `${v.key} → ${R.deptOf(v)}`);
check('UX Research stays in Product',
      all.filter(v => /^UX Research/i.test(v.sb || '') && /^Product Design/.test(R.deptOf(v))),
      v => `${v.key} ${v.s} → ${R.deptOf(v)}`);
// The data cannot exercise every branch — there is no Product (Web) card
// tagged Design today, so the guard above can never fire on real rows. State
// the rule directly so it is pinned regardless of what the dataset happens to
// contain this month.
const deptCases = [
  [{ t: 'Product (Mobile)', sb: 'Design', source: 'mobile' }, 'Product Design (Mobile)'],
  [{ t: 'Product',          sb: 'Design', source: 'mobile' }, 'Product Design (Mobile)'],
  [{ t: 'Design',                         source: 'web'    }, 'Product Design (Web)'],
  [{ t: 'Design',                         source: 'mobile' }, 'Product Design (Mobile)'],
  // mobile-only by decision — a web Product card tagged Design is a misfiling
  [{ t: 'Product (Web)',    sb: 'Design', source: 'web'    }, 'Product (Web)'],
  [{ t: 'Product',          sb: 'Design', source: 'web'    }, 'Product (Web)'],
  // UX Research is a different discipline
  [{ t: 'Product (Mobile)', sb: 'UX Research', source: 'mobile' }, 'Product (Mobile)'],
  // The board stated by the team name outranks the project the card sits on.
  // The FIRST of these two is the one that bites: _board only steers the Design
  // branches, so a non-Design card proves nothing about it — a mutation that
  // read the board off the project alone passed until this case was added.
  [{ t: 'Product (Mobile)', sb: 'Design', source: 'web' }, 'Product Design (Mobile)'],
  [{ t: 'Product (Mobile)', sb: null,     source: 'web' }, 'Product (Mobile)'],
  // the web team keeps its own spelling untouched
  [{ t: 'Product Design (Web)', sb: 'Platform', source: 'web' }, 'Product Design (Web)'],
];
check('deptOf follows the Design rule exactly',
      deptCases.filter(([v, want]) => R.deptOf(v) !== want)
               .map(([v, want]) => ({ key: `${JSON.stringify(v)} → ${R.deptOf(v)}, want ${want}` })));

fact('the Design promotion actually moved cards',
     all.some(v => /^Product Design \(Mobile\)$/.test(R.deptOf(v))), 'none');
check('no department carries two board suffixes',
      all.map(R.deptOf).filter(d => /\((Mobile|Web)\).*\((Mobile|Web)\)/.test(d))
         .map(d => ({ key: d })));

// Marketing splits in place inside the department tables. The parent row must
// carry NO data-mf, or the delegated modal handler fires instead of expanding —
// and the two halves must sum back to the parent, or the split is inventing or
// losing roles.
R.setMonth('active', '2026-08');
R.renderOverview();
const deptTbl = () => store['ov-atable'] || '';
check('a split row never carries data-mf',
      [...deptTbl().matchAll(/<tr[^>]*data-split="([^"]*)"[^>]*>/g)]
        .filter(m => /data-mf/.test(m[0])).map(m => ({ key: m[1] })));
// Driven by DEPT_SPLITS rather than a literal list, so removing an entry
// (as Product (Mobile) was, when Design became its own department) cannot
// leave a check silently asserting nothing.
Object.keys(R.DEPT_SPLITS).forEach(dept => {
  const key = 'active|overview|' + dept;
  if (!deptTbl().includes('data-split="' + key + '"')) return;   // nothing to split this month
  R.toggleDept(key);
  // data-mf is a single-quoted attribute, so & and ' arrive as entities.
  const unattr = s => s.replace(/&#39;/g, "'").replace(/&amp;/g, '&');
  const halves = [...deptTbl().matchAll(/dept-sub[^>]*data-mf='([^']*)'/g)]
    .map(m => JSON.parse(unattr(m[1]))).filter(mf => mf.val === dept);
  fact(dept + ' expands into exactly two halves', halves.length === 2, halves.length + ' sub-rows');
  const sum = halves.reduce((s, mf) => s + R.itemsFor(mf).length, 0);
  const whole = R.itemsFor({ bucket:'active', scope:'overview', dim:'dept', val:dept }).length;
  fact(dept + ': the halves sum back to the department', sum === whole, sum + ' vs ' + whole);
  check(dept + ': each half stays inside its department',
        halves.flatMap(mf => R.itemsFor(mf)).filter(v => R.deptOf(v) !== dept));
  R.toggleDept(key);
});

// The Active details table announces a split department's halves with a band row
// carrying the subtotal. The department cell spans the block via rowspan, so the
// bands MUST be counted in it — miss them and the table shears.
// Both detail tables carry the bands, so both are checked, and both on August —
// a month where every Marketing closure is Creative. That is the case the band
// rule turns on: a band LABELS its group rather than announcing a split, so it
// must still be drawn when the named subteam is the only group present.
// Requiring both halves suppressed it there, and August read as if the split
// had stopped working.
[{ label: 'Active', id: 'active-detail', month: '2026-08', headRows: 2,
   render: () => { R.setMonth('active', '2026-08'); R.renderActive(); },
   subtotal: /\d+ roles? · \d+ hires?/, items: () => R.activeItems('active') },
 { label: 'Closed', id: 'closed-detail', month: '2026-08', headRows: 1,
   render: () => { R.setMonth('closed', '2026-08'); R.renderClosed(); },
   subtotal: /\d+ closures?/, items: () => R.closedItems('closed') }
].forEach(t => {
  t.render();
  const detail = store[t.id] || '';
  const bands = [...detail.matchAll(/<tr[^>]*sub-band[^>]*>[\s\S]*?<\/tr>/g)].map(m => m[0]);
  // Derive the expected bands from the data rather than asserting a count, so
  // this keeps meaning the same thing as the months change under it. A split
  // department draws one band per non-empty half, and drawing NONE when the
  // named subteam holds everything is the bug this pins.
  const want = Object.entries(R.DEPT_SPLITS).flatMap(([dept, sp]) => {
    const mine = t.items().filter(v => R.deptOf(v) === dept);
    if (!mine.some(v => R.subBucket(v) === sp.label)) return [];
    return mine.some(v => R.subBucket(v) !== sp.label)
      ? [sp.label, dept.replace(/ \((Mobile|Web)\)$/, '')] : [sp.label];
  });
  fact(`${t.label}: every non-empty half gets a band`,
       want.length === bands.length && want.every(h => bands.some(b => b.includes(h))),
       `expected [${want}], got ${bands.length} band(s)`);
  fact(`${t.label}: the sample month actually exercises a band`, bands.length > 0,
       `${t.month} draws none`);
  check(`${t.label}: a band states its subtotal`,
        bands.filter(b => !t.subtotal.test(b)), b => b.slice(0, 50));
  // A band spans every column except the department one. Get it wrong and the
  // row is ragged — visible, but nothing errors, and easy to miss when a column
  // is added later. The count comes from a DATA row carrying the department
  // cell, not from the header: the Active table has two header rows, and
  // counting <th> there gives the wrong answer.
  const allRows = [...detail.matchAll(/<tr[^>]*>[\s\S]*?<\/tr>/g)].map(m => m[0]);
  const anchorRow = allRows.find(r => !/sub-band/.test(r) && /class="dep"/.test(r)) || '';
  const cols = (anchorRow.match(/<td[^>]*>/g) || []).length;
  const spanAttr = [...detail.matchAll(/sub-band[^>]*>[\s\S]*?<td colspan="(\d+)"/g)].map(m => +m[1]);
  fact(`${t.label}: the column count could be read`, cols > 2, String(cols));
  check(`${t.label}: a band spans every column but the department`,
        spanAttr.filter(n => n !== cols - 1)
                .map(n => ({ key: `colspan ${n}, table has ${cols} columns` })));
  const spans = [...detail.matchAll(/rowspan="(\d+)" class="dep"/g)].map(m => +m[1]);
  const bodyRows = (detail.match(/<tr[^>]*>/g) || []).length - t.headRows;
  fact(`${t.label}: the department rowspans cover every row including bands`,
       spans.reduce((a, b) => a + b, 0) === bodyRows,
       spans.reduce((a, b) => a + b, 0) + ' vs ' + bodyRows);
});

// The split keys off the SUBTEAM, so a title that plainly belongs to a half
// lands in the other one whenever the subteam is spelled differently — and
// nothing complains, because a card in the wrong half is still a card. Zero of
// these today; the check exists for the day Jira gains 'Brand Creative' or
// 'UX/UI' in one of these departments.
Object.entries(R.DEPT_SPLITS).forEach(([dept, sp]) => {
  check(`${dept}: no ${sp.label}-looking role sits outside ${sp.label}`,
        all.filter(v => R.deptOf(v) === dept && sp.looks.test(v.s || '')
                        && R.subBucket(v) !== sp.label),
        v => `${v.key} "${(v.s || '').slice(0, 26)}" sb=${JSON.stringify(v.sb)}`);
});

// Reconciliation, hand-written in excel_source.py. If a key moves in Jira the
// merge quietly goes back to counting one requisition twice.
// A superseded card may only be present if it has been REOPENED — REC-274 sat
// in On hold when the sheet recorded its openings cancelled, then came back on
// 20 August with a fresh start date. Present AND still stopped would mean the
// drop silently failed and the requisition is counted twice.
const SUPERSEDED = ['REC-274', 'REC-356'];
check('a superseded card is absent unless it was reopened',
      SUPERSEDED.map(k => byKey[k]).filter(Boolean)
        .filter(v => v.cxd || v.fcd || v.hd || v.rd)
        .filter(v => !(v.pk && byKey[v.pk] && !end(byKey[v.pk]))),
      v => `${v.key} still stopped (${end(v)})`);
fact('cancelled openings are attached to REC-540',
     all.filter(v => v.pk === 'REC-540').length >= 2, 'nothing attached');

// Episodes: boundaries, and the show-the-previous-one-until-it-closes rule.
const ep1 = R._episodeAt(R._episodeIndex(2026, 1));
const ep2 = R._episodeAt(ep1.i + 1), ep3 = R._episodeAt(ep1.i + 2);
fact('episode 1 runs February to May',
     ep1.no === 1 && ep1.from === '2026-02-01' && ep1.to === '2026-05-31',
     `${ep1.no}: ${ep1.from}…${ep1.to}`);
fact('episode 2 runs June to September',
     ep2.no === 2 && ep2.from === '2026-06-01' && ep2.to === '2026-09-30',
     `${ep2.no}: ${ep2.from}…${ep2.to}`);
fact('episode 3 runs October to January, crossing the year',
     ep3.no === 3 && ep3.from === '2026-10-01' && ep3.to === '2027-01-31',
     `${ep3.no}: ${ep3.from}…${ep3.to}`);
R.setMonth('closed', '2026-08');
fact('an unfinished episode shows the previous one',
     R._episodeLabel('closed').startsWith('Episode 1:'), R._episodeLabel('closed'));
fact('the episode sample is not empty', R.closedItemsEpisode('closed').length > 0, 'no closures');

// An episode reaching back before the data starts has no honest average, so the
// KPI row drops to the count alone. January resolves to October 2025 - January
// 2026, three of whose months hold nothing at all.
R.setMonth('closed', '2026-01');
fact('January has no averages to show', !R._episodeHasFullData('closed'),
     R._episodeLabel('closed'));
fact('January still reports its closures', R.closedItems('closed').length > 0, 'none');
R.setMonth('closed', '2026-05');
fact('a fully covered episode keeps its averages', R._episodeHasFullData('closed'),
     R._episodeLabel('closed'));

// Presentation. Tooltips are injected with textContent, so markup reaches the
// reader as literal characters — this shipped as "<b>Episode 1</b>".
const tips = [...html.matchAll(/data-tip="([^"]*)"/g)].map(m => m[1]);
fact('tooltips exist at all', tips.length > 0, 'none found');
check('no tooltip contains markup textContent cannot render',
      tips.filter(t => t.includes('<')), t => t.slice(0, 40) + '…');
// Long explanations are broken into paragraphs with &#10; in the attribute, which
// only renders because #tip carries white-space:pre-line. Lose either half and it
// collapses back into one unreadable block — silently, since nothing errors.
// `tips` holds the RAW attribute values, where a break is the entity &#10; — the
// browser decodes it on read, this does not. A PARAGRAPH break is a double one;
// requiring merely "some break" passed a tooltip whose paragraphs had been
// glued back together and only the last single newline survived.
const paragraphBreaks = t => (t.match(/&#10;&#10;|\n\n/g) || []).length;
check('long tooltips are split into paragraphs',
      tips.filter(t => t.length > 200 && paragraphBreaks(t) < 1),
      t => `${t.slice(0, 34)}… (${paragraphBreaks(t)} breaks)`);
fact('the tooltip preserves the line breaks it is given',
     /#tip\s*\{[^}]*white-space:\s*pre-line/.test(html), 'white-space:pre-line missing from #tip');

// Freezing the period. It used to be a bare in-memory flag that only greyed out
// the controls, while its own tooltip promised the reader would land on the month
// you chose — nothing persisted it, so it survived neither a reload nor a link.
fact('the freeze writes the period into the URL', /function _periodHash\(/.test(html),
     '_periodHash missing');
fact('the freeze is restored on load', /_readPeriodHash\(\);/.test(html),
     'the bootstrap never reads the fragment');
fact('the fragment is read after the selects are built',
     html.indexOf('_populatePeriodSelects();') < html.lastIndexOf('_readPeriodHash();'),
     'read before the month dropdown exists');
fact('the fragment is read before the first render',
     html.lastIndexOf('_readPeriodHash();') < html.lastIndexOf('renderAll();'),
     'the page would flash the default period first');

// Period pickers. These were silently empty once, and the page still rendered,
// so the report was declared working.
const opts = id => [...(store[id] || '').matchAll(/value="([^"]+)"/g)].map(m => m[1]);
const months = opts('f-overview-month');
fact('the month picker is populated', months.length > 0, 'empty');
check('the month picker starts no earlier than January 2026',
      months.filter(m => m < '2026-01').map(m => ({ key: m })));
fact('the month picker runs newest first',
     months.length > 1 && months[0] > months[months.length - 1],
     `${months[0]} … ${months[months.length - 1]}`);

// ── the Month | Episode toggle ──
// The whole point of offering only FINISHED episodes is that the tables and the
// TTF/TTH cards then describe the same window. Those cards fall back to the
// previous episode whenever the selected one is still running, so a picker that
// let a running episode through would put two different episodes on one screen
// with nothing saying so.
const TODAY_ISO = '2026-09-11';   // matches the report's TODAY; see _finishedEpisodes
const eps = R._finishedEpisodes();
fact('an episode is on offer at all', eps.length > 0, 'the picker would be empty');
check('every offered episode has ENDED',
      eps.filter(e => e.to >= TODAY_ISO).map(e => ({ key: `Episode ${e.no} ends ${e.to}` })));
check('no offered episode reaches back past the data floor',
      eps.filter(e => e.from < '2026-01-01').map(e => ({ key: `Episode ${e.no} starts ${e.from}` })));
fact('the episode list runs newest first',
     eps.length < 2 || eps[0].from > eps[eps.length - 1].from,
     `${eps[0].from} … ${eps[eps.length - 1].from}`);

R.setGrain('episode');
const epOpts = opts('f-overview-month');
fact('the Episode toggle refills the dropdown',
     epOpts.length === eps.length && epOpts.every(v => /^ep:-?\d+$/.test(v)),
     `${epOpts.length} option(s): ${epOpts.join(', ')}`);
fact('the Episode dropdown offers no months', !epOpts.some(v => /^\d{4}-\d{2}$/.test(v)),
     epOpts.join(', '));

R.onPeriodPick('ep:' + eps[0].i);
fact('picking an episode sets its exact window',
     R.PERIOD.f === eps[0].from && R.PERIOD.t === eps[0].to,
     `${R.PERIOD.f} … ${R.PERIOD.t}`);
// This is the check that makes the "finished episodes only" rule worth having.
fact('the TTF/TTH cards land on the SAME episode the tables show',
     R._episodeHasFullData('closed') &&
     R._episodeLabel('closed').indexOf('Episode ' + eps[0].no) === 0,
     `cards say "${R._episodeLabel('closed')}", period is ${R.PERIOD.f}…${R.PERIOD.t}`);

// A frozen episode must travel as an EPISODE, not as a bare date range — the
// window alone restores the right data under a control reading "Month…".
const epHash = R._periodHash();
fact('freezing an episode writes it into the link', epHash.includes('ep=' + eps[0].i), epHash);
R.setMonth('overview', '2026-08');
fact('choosing a month clears the episode', R.PERIOD.ep === null, String(R.PERIOD.ep));
fact('choosing a month returns the control to Month', R.GRAIN() === 'month', R.GRAIN());
location.hash = epHash;
R._readPeriodHash();
fact('a frozen link restores the episode, not a bare range',
     R.PERIOD.ep === eps[0].i && R.GRAIN() === 'episode',
     `ep=${R.PERIOD.ep} grain=${R.GRAIN()}`);
fact('the restored dropdown shows the episode',
     store['f-overview-month#value'] === 'ep:' + eps[0].i,
     store['f-overview-month#value']);
// Someone will hand-edit one of these links. An `ep` that does not match the
// range must not be believed, and must not survive into the next freeze.
if (R.locked()) R.toggleLock();
location.hash = `#period=custom&from=${eps[0].from}&to=2026-03-31&ep=${eps[0].i}`;
R._readPeriodHash();
fact('an episode index that contradicts the range is rejected',
     R.PERIOD.ep === null && R.GRAIN() === 'month',
     `ep=${R.PERIOD.ep} grain=${R.GRAIN()}`);
fact('...and does not leak into the next frozen link',
     !R._periodHash().includes('ep='), R._periodHash());
if (R.locked()) R.toggleLock();
R.toggleLock();
fact('freezing disables the Month|Episode toggle',
     SEGS['f-overview-grain'].every(b => b.disabled),
     SEGS['f-overview-grain'].map(b => b.disabled).join(','));

// ── candidate source, and the free-text field behind "Other" ──
// Jira's picklist ends in a literal `Other` with the real answer typed into
// "Candidate source [Other]". These are pure-function checks so they stay
// meaningful even on a dataset with no Other cards — the LOCAL build drops
// every pre-July card in favour of spreadsheet rows, and the spreadsheet has
// no source column at all, so the local preview has none. CI runs this against
// the freshly-refreshed report, where they exist.
const csCases = [
  [{ cs: 'Dou' },                              'Dou',            'plain picklist value'],
  [{ cs: 'Other', cs_other: 'Telegram' },      'Telegram',       'Other is replaced by the free text'],
  [{ cs: 'Other', cs_other: '  Telegram  ' },  'Telegram',       'the free text is trimmed'],
  [{ cs: 'Other', cs_other: '' },              'Other',          'Other with no free text stays Other'],
  [{ cs: 'Other' },                            'Other',          'Other with a missing field stays Other'],
  [{ cs: null },                               '(Not specified)', 'nothing at all'],
  // Deliberate: the picklist is what says "Other". Text with no pick is a note.
  [{ cs: null, cs_other: 'внутрішній' },       '(Not specified)', 'free text alone does NOT become a source'],
];
check('the candidate source resolves as specified',
      csCases.filter(([v, want]) => R.csLabel(v) !== want)
             .map(([v, want, why]) => ({ key: `${why}: got ${JSON.stringify(R.csLabel(v))}, want ${JSON.stringify(want)}` })));
// A cell keeps its dash; a bucket name never can.
fact('an empty source renders as a dash, not as "(Not specified)"',
     R.csCell({ cs: null }).includes('dash-val'), R.csCell({ cs: null }));

// The free text is typed by hand and lands in element bodies AND in attributes
// (title=, data-tip=). An apostrophe alone used to break _attr's quoting.
const nasty = { cs: 'Other', cs_other: 'a" onerror="x <b> & \'q\'' };
check('free text is escaped before it reaches the markup',
      ['"', '<', '>'].filter(c => R.csCell(nasty).includes(c)).map(c => ({ key: `raw ${c} in the cell` })));

// The chart's labels and the drill-down's filter must be the SAME string, or
// clicking a bar opens an empty modal.
//
// Widen the period FIRST. This check inherits whatever window the previous
// checks left behind — which was a two-month slice — and the handful of cards
// carrying an `Other` source fell outside it, so the comparison was vacuous
// and a real mutation (chart labelled by csLabel, filter by the old v.cs)
// slipped through it.
R.PERIOD.p = 'custom'; R.PERIOD.f = '2026-01-01'; R.PERIOD.t = '2026-12-31'; R.PERIOD.ep = null;
// Deliberately NOT via hsRender(). That function, hsPopulate and hsReset are
// dead: the page carries their CSS but no #hs-cards / #hs-chart / #hs-dept
// elements and nothing ever calls them. Driving the check through hsRender
// only worked because this harness returns a stub for every id, never null —
// it would throw in a real browser. So compare the buckets directly.
const buckets = [...new Set(R._hsBase().map(R.csLabel))];
fact('the source sample is not empty', buckets.length > 1, `${buckets.length} bucket(s)`);
check('every source bucket is reachable by clicking it',
      buckets.filter(b => R.itemsFor({ bucket: 'closed', scope: 'closed', cs: b }).length !==
                          R._hsBase().filter(v => R.csLabel(v) === b).length)
             .map(b => ({ key: b })));

// The only place the source is actually VISIBLE is the "Job boards" column of
// the Closed Roles table — so assert the substitution reaches the rendered
// markup, not merely the function.
R.renderClosedDetail();
const jobCells = [...(store['closed-detail'] || '').matchAll(/<tr[^>]*>[\s\S]*?<\/tr>/g)]
  .map(m => [...m[0].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)].map(c => c[1].replace(/<[^>]*>/g, '').trim()))
  .filter(tds => tds.length > 8).map(tds => tds[tds.length - 2]);
const wantVisible = [...new Set(R._hsBase().filter(v => (v.cs || '').trim() === 'Other' && (v.cs_other || '').trim())
                                           .map(v => v.cs_other.trim()))];
check('the Other free text reaches the Job boards column',
      wantVisible.filter(t => !jobCells.includes(t)).map(t => ({ key: t })));
fact('the Job boards column was found at all', jobCells.length > 0, '0 cells');

// THE point of this check: the report is published to a public mirror, and
// this free-text field demonstrably collects candidate names — two cards in
// Jira today read "Candidate - <full name>" (under a different picklist value,
// so they are not displayed). If one is ever typed under `Other`, it would be
// printed on a public page. Fail the build instead.
const PERSONAL = [/\bcandidat/i, /кандидат/i, /[^\s@]+@[^\s@]+\.[a-z]{2,}/i, /\+?\d[\d ()-]{7,}/];
check('no displayed source looks like personal data',
      R._allCards().filter(v => PERSONAL.some(re => re.test(R.csLabel(v))))
                   .map(v => ({ key: `${v.key}: ${JSON.stringify(R.csLabel(v))}` })), x => x.key);

console.log(failed ? `\n${failed} check(s) FAILED` : '\nall checks passed');
process.exit(failed ? 1 : 0);
