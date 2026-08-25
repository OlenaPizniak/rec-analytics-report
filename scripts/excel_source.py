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

import csv
import os
import re
import datetime

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


def _date(s):
    """DD.MM.YYYY → YYYY-MM-DD. The sheet also holds a few D.M.YYYY."""
    m = re.match(r'^(\d{1,2})[./](\d{1,2})[./](\d{4})$', (s or '').strip())
    if not m:
        return None
    return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}'


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
                           _date(_get(r, 'Published'))), []).append(r)

    closed, canceled = [], []
    for gi, (key, members) in enumerate(sorted(groups.items(), key=lambda kv: str(kv[0])), 1):
        team, vacancy, published = key
        parent = f'XL-{gi}'
        for mi, r in enumerate(members):
            is_sub = mi > 0
            es = _employee_status(_get(r, 'Staff/Non-staff'))
            status = _get(r, 'Status')
            closed_at = _date(_get(r, 'Closed'))
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
                'fcd_c': _date(_get(r, '1st contact')),
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
            (canceled if status == 'Canceled' else closed).append(item)

    return closed, canceled


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
