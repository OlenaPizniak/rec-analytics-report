#!/usr/bin/env python3
"""Historical roles for 2026 H1, read from the recruiters' own spreadsheet.

Jira was not filled in consistently before roughly April 2026 — for January it
holds 13 of the 37 roles the team actually worked. The spreadsheet is the record
for that period, so the two sources are split by a hard cutoff ON THE CARD, not
on the reporting period:

    ended on or before CUTOFF  →  this file is the source, Jira rows are dropped
    still open after CUTOFF    →  Jira is the source

That split is what keeps the same role from being counted twice: every card
belongs to exactly one source.

The CSV is one row per HIRE, not per role. Rows sharing (Team, Vacancy,
Published) are one requisition filled several times, so they are emitted as a
parent card plus sub-tasks — the same shape Jira uses. The render layer then
counts them without knowing where they came from: one role, N headcount on the
Active tab, N closures on the Closed tab.
"""

import collections
import csv
import os
import re
import datetime

# ── Both CSVs are FINAL. ────────────────────────────────────────────────────
# January to June 2026 is a closed period: the numbers are agreed and will not
# change, so these files are a finished snapshot rather than a mirror of a live
# sheet. Nobody has to re-export them, and nothing here goes stale on its own.
# If that ever changes — a correction to H1, or the same treatment extended to
# H2 — the export needs re-copying by hand and the traps documented in _date()
# and load_canceled() checking again, because both were silent.
CUTOFF = '2026-06-30'          # last day the spreadsheet is authoritative for
FLOOR = '2026-01-01'           # nothing before this is reported
CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'final_data_2026_h1.csv')


def _get(row, key):
    """Headers carry stray trailing spaces ('Team ', 'Subteam ')."""
    for k in (key, key + ' '):
        if k in row:
            return (row[k] or '').strip()
    return ''


def _date(s, field=None):
    """A date from either sheet → YYYY-MM-DD. Empty input returns None.

    THE SEPARATOR DECIDES THE ORDER, and that is not a guess — it is what the
    two exports actually do, across 565 values with no exceptions:

        dots  D.M.YYYY   hires sheet everywhere; cancellations `Published`
        slash M/D/YYYY   cancellations `Closed` only

    Anything impossible raises instead of returning a value. The previous
    version accepted any three numbers and formatted them blindly, so a US date
    fed to it produced '2026-17-02' and '31.02.2026' produced '2026-02-31' —
    dates that sort into the wrong month and never surface as an error. A build
    that stops with the offending value named is strictly better than a
    dashboard that is quietly wrong about when roles closed.
    """
    raw = (s or '').strip()
    if not raw:
        return None
    m = re.match(r'^(\d{1,2})([./])(\d{1,2})\2(\d{4})$', raw)
    if not m:
        raise SystemExit(f'unparseable date {raw!r}' + (f' in column {field!r}' if field else ''))
    a, sep, b, year = m.group(1), m.group(2), m.group(3), m.group(4)
    day, month = (a, b) if sep == '.' else (b, a)
    try:
        return datetime.date(int(year), int(month), int(day)).isoformat()
    except ValueError as e:
        raise SystemExit(
            f'impossible date {raw!r}' + (f' in column {field!r}' if field else '')
            + f' — read as day={day} month={month} year={year} ({e}). '
              'Dots mean D.M.YYYY, slashes mean M/D/YYYY; if this export uses '
              'another order, teach _date() about it rather than letting it through.')


def _require(problems, label):
    """Stop the build listing every bad row, instead of emitting a quiet guess.

    Both sheets are hand-maintained exports, so a column can be renamed, blanked
    or shifted at any time. Every such change here has been silent: a US date
    became month 17, an empty employment column turned Expert consultations into
    vacancies. None of it showed up on the dashboard as anything but a slightly
    different number.
    """
    if problems:
        head = '\n  '.join(problems[:12])
        more = f'\n  … and {len(problems) - 12} more' if len(problems) > 12 else ''
        raise SystemExit(f'{label}: {len(problems)} row(s) could not be read\n  {head}{more}')


def _reason(raw):
    """Normalise to the legacy spelling the render layer expects.

    'Сonsultation' appears once with a CYRILLIC Es (U+0421) — it would otherwise
    become its own category.
    """
    r = (raw or '').strip().replace('С', 'C')
    return {'extension': 'Extention', 'replacement': 'Replacement',
            'consultation': 'Consultation'}.get(r.lower(), r or None)


def _priority(raw):
    """The sheet shouts its priorities; Jira title-cases them.

    prBucket() in the render layer matches Jira's spelling exactly, so an
    un-normalised 'HIGH' silently becomes "Not specified" — all 178 rows landed
    there. 'LIGHT' is not even the same word as Jira's 'Low'.
    """
    return {'high': 'High', 'medium': 'Medium',
            'light': 'Low', 'low': 'Low'}.get((raw or '').strip().lower())


def _employee_status(raw):
    """esBucket() compares against 'Staff' / 'Non-staff' / 'Expert' verbatim,
    so the sheet's lowercase 'staff' / 'non-staff' fell through to 'n/a'."""
    return {'staff': 'Staff', 'non-staff': 'Non-staff',
            'expert': 'Expert'}.get((raw or '').strip().lower()) or (raw or None)


def _team(raw):
    """The sheet abbreviates one department that Jira spells out. Left as two
    names it shows up as two rows on Overview."""
    t = (raw or '').strip()
    return {'E-com': 'E-commerce'}.get(t, t) or None


def _kind(employee_status, is_sub):
    """'Expert' is the spreadsheet's third employment type → Recruitment
    Assignment, matching how esBucket() treats it on the Jira side."""
    ra = (employee_status or '').strip().lower() == 'expert'
    if ra:
        return 'ra_sub' if is_sub else 'ra'
    return 'op_sub' if is_sub else 'op'


def load(path=None):
    """Return items shaped like the updater's own, split into (closed, canceled)."""
    path = path or CSV_PATH
    if not os.path.exists(path):
        print(f'  ! spreadsheet not found: {path}')
        return [], []

    with open(path, encoding='utf-8') as f:
        rows = [r for r in csv.DictReader(f) if _get(r, 'Vacancy')]

    # One requisition = rows sharing team + vacancy + opening date.
    groups = {}
    for r in rows:
        groups.setdefault((_team(_get(r, 'Team')), _get(r, 'Vacancy'),
                           _date(_get(r, 'Published'), 'Published')), []).append(r)

    closed, canceled = [], []
    problems = []
    for gi, (key, members) in enumerate(sorted(groups.items(), key=lambda kv: str(kv[0])), 1):
        team, vacancy, published = key
        parent = f'XL-{gi}'
        for mi, r in enumerate(members):
            is_sub = mi > 0
            es = _employee_status(_get(r, 'Staff/Non-staff'))
            status = _get(r, 'Status')
            closed_at = _date(_get(r, 'Closed'), 'Closed')
            item = {
                'key': parent if not is_sub else f'{parent}-{mi}',
                's': vacancy or None,
                # Team carries the board in its name; keep the badge working.
                'source': 'web' if '(Web)' in team else 'mobile',
                'type': 'subtask' if is_sub else 'position',
                'kind': _kind(es, is_sub),
                'st': status,
                'pr': _priority(_get(r, 'Priority')),
                'sn': _get(r, 'Level') or None,
                'r': _reason(_get(r, 'Reason')),
                'es': es or None,
                'rec': None, 'src': None, 'so': None,
                'sd': published,
                'sde': published,
                'ed': None,
                'fcd': closed_at if status != 'Canceled' else None,
                'fcd_c': _date(_get(r, '1st contact'), '1st contact'),
                'rd': None,
                'hd': closed_at if status != 'Canceled' else None,
                # Cancellations carry their date here, the same field the Jira
                # side fills from status history.
                'cxd': closed_at if status == 'Canceled' else None,
                'cs': _get(r, 'Job boards') or None,
                'cs_other': None,
                'h': 1,
                't': team or None,
                'sb': None,              # sub-teams are deliberately not used
                'cr': published,
                'origin': 'sheet',       # provenance, for debugging
            }
            if is_sub:
                item['pk'] = parent
            if not closed_at:
                problems.append(f'{vacancy!r} ({team}, opened {published}): '
                                f'empty Closed date')
            if not es:
                problems.append(f'{vacancy!r} ({team}, opened {published}): '
                                f'no Staff/Non-staff value')
            (canceled if status == 'Canceled' else closed).append(item)

    _require(problems, f'{os.path.basename(path)}')
    return closed, canceled


CANCELED_CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'canceled_mobile_2026_h1.csv')

# Requisitions the cancellation sheet describes that Jira also holds, recorded
# there under a different title and a status that never became "Canceled".
# Matching cannot find these — the titles disagree (one is a Ukrainian sentence)
# and so do the dates — so they are stated outright. Confirmed with Yaroslava
# case by case; see the note against each.
#
# The sheet's rows are ADDITIONAL openings on this requisition: two people were
# found (REC-540, REC-541 → Done) and the rest of the headcount was cancelled.
# Attaching keeps it one role with four openings instead of two roles.
CANCELED_ATTACH_TO = {
    ('Content', 'Gym Trainer', '2026-05-25'): 'REC-540',
}
# The SAME openings as the sheet's rows: left sitting in "On hold" in Jira, but
# actually cancelled. The sheet is the record, so the Jira pair goes.
# (Running Coach — REC-274 wanted 2 people, REC-356 is its only sub-task.)
CANCELED_SUPERSEDES_JIRA = {'REC-274', 'REC-356'}


def requisition_index(items):
    """(team, vacancy, opening date) → parent key, for the family heads in `items`.

    The two sheets describe the same requisitions from different angles: one
    lists the openings that were filled, the other the ones that were cancelled.
    A requisition that had both appears in both files, and giving the cancelled
    rows their own XC- key split it into two roles — Graphic Designer, opened
    25.03, showed as one role of three hires and another of three cancellations.
    Both files use the same columns for team, vacancy and opening date, so the
    match needs no heuristic.
    """
    idx = {}
    for it in items:
        if it.get('pk'):
            continue
        idx[(it['t'], it['s'], it['sd'])] = it['key']
    return idx


def load_canceled(path=None, attach_to=None):
    """Cancelled mobile requisitions for 2026 H1, one row per cancelled opening.

    Same shape as load(), so the render layer cannot tell the two sheets apart.
    Grouping matches the hires sheet — team + vacancy + opening date, seniority
    deliberately NOT part of the key, since a requisition can be opened for
    mixed levels (this happens twice in the hires sheet too).
    """
    path = path or CANCELED_CSV_PATH
    if not os.path.exists(path):
        print(f'  ! cancellation sheet not found: {path}')
        return []

    with open(path, encoding='utf-8') as f:
        rows = [r for r in csv.DictReader(f) if _get(r, 'Vacancy')]

    groups = {}
    for r in rows:
        groups.setdefault((_team(_get(r, 'Team')), _get(r, 'Vacancy'),
                           _date(_get(r, 'Published'), 'Published')), []).append(r)

    out, problems = [], []
    es_col = collections.Counter()
    for gi, (key, members) in enumerate(sorted(groups.items(), key=lambda kv: str(kv[0])), 1):
        team, vacancy, published = key
        # Two ways this requisition may already exist: as a Jira card (named
        # outright, since nothing can match it) or as a row in the hires sheet
        # (matched on the key both files share).
        attach = CANCELED_ATTACH_TO.get(key) or (attach_to or {}).get(key)
        # Attached rows are all openings of an existing Jira requisition, so
        # every one of them is a sub-task of it. Standalone ones keep the usual
        # first-row-is-the-parent shape.
        parent = attach or f'XC-{gi}'
        for mi, r in enumerate(members):
            is_sub = bool(attach) or mi > 0
            # The employment type sits in the LAST column of this export, headed
            # 'Empoyee ID'; the column actually named Staff/Non-staff is empty in
            # all 38 rows. Prefer the proper column so a corrected export wins.
            # Which column actually carried it is tracked, so a corrected
            # export shows up in the build log instead of passing unnoticed.
            proper, misplaced = _get(r, 'Staff/Non-staff'), _get(r, 'Empoyee ID')
            es_col['Staff/Non-staff' if proper else
                   ('Empoyee ID' if misplaced else 'MISSING')] += 1
            es = _employee_status(proper or misplaced)
            cancelled_at = _date(_get(r, 'Closed'), 'Closed')
            if not es:
                problems.append(f'{vacancy!r} ({team}, opened {published}): '
                                f'employment type missing from BOTH '
                                f'Staff/Non-staff and Empoyee ID')
            if not cancelled_at:
                problems.append(f'{vacancy!r} ({team}, opened {published}): '
                                f'empty Closed date on a cancelled row')
            item = {
                'key': parent if (not is_sub) else f'{parent}-c{mi + 1}',
                's': vacancy or None,
                'source': 'web' if '(Web)' in (team or '') else 'mobile',
                'type': 'subtask' if is_sub else 'position',
                'kind': _kind(es, is_sub),
                'st': 'Canceled',
                'pr': _priority(_get(r, 'Priority')),
                'sn': _get(r, 'Level') or None,
                'r': _reason(_get(r, 'Reason')),
                'es': es or None,
                'rec': None, 'src': None, 'so': None,
                'sd': published, 'sde': published, 'ed': None,
                'fcd': None, 'fcd_c': None, 'rd': None, 'hd': None,
                'cxd': cancelled_at,
                'cs': None, 'cs_other': None,
                'h': 1,
                't': team or None,
                'sb': None,
                'cr': published,
                'origin': 'sheet',
            }
            if is_sub:
                item['pk'] = parent
            out.append(item)

    _require(problems, os.path.basename(path))
    if es_col.get('Empoyee ID'):
        print(f"  ! {os.path.basename(path)}: employment type read from the "
              f"'Empoyee ID' column for {es_col['Empoyee ID']} row(s) — the "
              f"Staff/Non-staff column is empty there")
    return out


def drop_superseded_by_cancellations(*arrays):
    """Remove the Jira cards the cancellation sheet replaces outright.

    These end AFTER the cutoff, so drop_superseded() leaves them alone; without
    this they would be counted alongside the sheet rows describing the very same
    openings.
    """
    removed = []
    for arr in arrays:
        keep = []
        for it in arr:
            if it.get('origin') != 'sheet' and it['key'] in CANCELED_SUPERSEDES_JIRA:
                removed.append(it['key'])
            else:
                keep.append(it)
        arr[:] = keep
    return removed


def validate_reconciliation(linked, superseded, *arrays):
    """Check the hand-written reconciliation still describes reality.

    CANCELED_ATTACH_TO and CANCELED_SUPERSEDES_JIRA name Jira issues directly,
    because no matcher can find them — the titles disagree and so do the dates.
    That makes them the one part of this pipeline that goes stale silently: if a
    key is renamed, deleted, or moved between boards, an attach quietly turns
    its rows into a role of their own and a supersede quietly stops removing the
    duplicate. Either way the dashboard keeps working and just reports too many
    roles, which is the failure mode hardest to notice.
    """
    keys = {it['key'] for arr in arrays for it in arr}
    problems = []
    for req, target in CANCELED_ATTACH_TO.items():
        if target not in keys:
            problems.append(f'CANCELED_ATTACH_TO {req} → {target}: no such card in the '
                            f'dataset, so its rows would stand as a separate role')
    missing = CANCELED_SUPERSEDES_JIRA - set(superseded)
    for key in sorted(missing):
        problems.append(f'CANCELED_SUPERSEDES_JIRA {key}: nothing was removed — either it '
                        f'is gone from Jira (drop it from the set) or it never arrived')
    still_here = CANCELED_SUPERSEDES_JIRA & keys
    for key in sorted(still_here):
        problems.append(f'CANCELED_SUPERSEDES_JIRA {key}: still present after the drop')
    if problems:
        raise SystemExit('stale reconciliation:\n  ' + '\n  '.join(problems))
    # The identity matcher is a heuristic, so it warns rather than fails: titles
    # can legitimately change. Zero pairs where there were three means it broke.
    if not linked:
        print('  ! link_split_requisitions matched nothing — it linked 3 pairs when '
              'written; check whether titles or start dates moved')


def _match_key(item):
    """Identity of a requisition, independent of which system recorded it.

    Title + opening date + seniority. Titles are compared with whitespace
    collapsed because the sheet and Jira both carry e.g. 'Content  Production
    Specialist' with a double space, and one of them could be cleaned up later.
    """
    title = re.sub(r'\s+', ' ', (item.get('s') or '')).strip().lower()
    start = item.get('sde') or item.get('sd')
    level = (item.get('sn') or '').strip().lower()
    if not title or not start:
        return None
    return (title, start, level)


def link_split_requisitions(sheet_items, *jira_arrays):
    """Reunite a requisition that the cutoff cut in half.

    A requisition whose early openings were filled before the cutoff (so they
    come from the sheet) but whose Jira card outlived it appears twice: once as
    the sheet family, once as the surviving Jira card. Nothing is duplicated at
    the headcount level — each opening is still counted once — but the SAME
    requisition is reported as two roles.

    They share no key, so they are matched on identity instead, then the Jira
    side is re-pointed at the sheet parent and folds into it as an opening.

    A matched Jira card may itself have sub-tasks that survived the cutoff.
    Those have to be re-pointed too: _rolesFrom resolves one level of parent,
    so leaving them attached to a card that is now itself a sub-task would
    split the requisition right back into two roles.
    """
    parents = {}
    for it in sheet_items:
        if it.get('pk'):
            continue                       # only the family head is a target
        k = _match_key(it)
        if k:
            parents.setdefault(k, it['key'])

    cards = [it for arr in jira_arrays for it in arr]
    children = {}
    for it in cards:
        if it.get('pk'):
            children.setdefault(it['pk'], []).append(it)

    linked = []
    for it in cards:
        if it.get('origin') == 'sheet':
            continue
        target = parents.get(_match_key(it))
        if not target or target == it['key']:
            continue
        # A Jira sub-task is matched on its OWN identity, which is the point:
        # REC-592 opened 12.05 and hung off a parent opened 07.07, so it was
        # reported under a July requisition while its May twin stood separately.
        # Its own dates say which requisition it belongs to.
        it['pk'] = target
        it['kind'] = 'ra_sub' if it.get('kind') in ('ra', 'ra_sub') else 'op_sub'
        for ch in children.get(it['key'], []):
            ch['pk'] = target              # re-point, or the chain breaks
        linked.append((it['key'], target, len(children.get(it['key'], []))))
    return linked


def ended_at(item):
    """Date a card stopped being worked, whichever way it ended."""
    dates = [d for d in (item.get('fcd'), item.get('hd'),
                         item.get('rd'), item.get('cxd')) if d]
    return min(dates) if dates else None


def drop_superseded(*arrays):
    """Remove Jira cards the spreadsheet is authoritative for.

    Anything that ended on or before CUTOFF now comes from the sheet; keeping the
    Jira copy would double-count the same role.
    """
    removed = 0
    for arr in arrays:
        keep = []
        for it in arr:
            e = ended_at(it)
            if it.get('origin') != 'sheet' and e and e <= CUTOFF:
                removed += 1
            else:
                keep.append(it)
        arr[:] = keep
    return removed


if __name__ == '__main__':
    cv, cx = load()
    print(f'closed:   {len(cv)} cards')
    print(f'canceled: {len(cx)} cards')
    roles = len({i.get('pk') or i['key'] for i in cv + cx})
    print(f'roles:    {roles}')
    months = {}
    for i in cv + cx:
        e = ended_at(i)
        if e:
            months[e[:7]] = months.get(e[:7], 0) + 1
    for m in sorted(months):
        print(f'  {m}  {months[m]}')
