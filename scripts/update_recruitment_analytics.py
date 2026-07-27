#!/usr/bin/env python3
"""
Auto-updater for the Recruitment Analytics Report (mobile REC + web WRP).

Fetches fresh data from TWO Jira boards and rewrites the data blocks in
reports/Recruitment_Analytics_Report.html between AUTO_*_START / END markers.

  • REC (project "Recruitment Team") — mobile track
        Open position          → kind 'op'
        Vacancy sub-task        → kind 'op_sub'
        Recruitment Assignment  → kind 'ra'   (consultant track)
        Recruitment Assignment sub-task → kind 'ra_sub'
  • WRP (project "Web Recruitment Planning") — web track
        Vacancy                 → kind 'op'
        Consultant              → kind 'ra'   (consultant track; no sub-tasks on web)

Every emitted item carries source:'mobile'|'web'. WRP values are normalized to
the REC (mobile) taxonomy via the mappers below so the existing render layer
works unchanged (reason stays the legacy spelling "Extention", etc.).

Required env vars:
  JIRA_EMAIL       — Atlassian account email
  JIRA_API_TOKEN   — Atlassian API token (https://id.atlassian.com/manage/api-tokens)

Usage:
  JIRA_EMAIL=... JIRA_API_TOKEN=... python3 scripts/update_recruitment_analytics.py
"""

import os
import re
import sys
import json
import base64
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError

# ── Configuration ───────────────────────────────────────────
JIRA_HOST = "https://newsiteam.atlassian.net"
HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "reports", "Recruitment_Analytics_Report.html"
)

# Custom field IDs. The 9 shared fields have IDENTICAL ids on both boards, so a
# single field list works for both. Board-specific fields are listed separately.
F = {
    # ── shared (same customfield id on REC + WRP) ──
    'start_date':      'customfield_11223',
    'recruiter':       'customfield_13935',
    'fcd':             'customfield_22878',   # Factual close date
    'fcd_contact':     'customfield_23407',   # First contact date (for TTH)
    'hiring_manager':  'customfield_23509',
    'sourcer':         'customfield_23510',
    'num_hires':       'customfield_23545',
    'employee_status': 'customfield_23581',   # Staff / Non-staff / (Expert on REC only)
    'cand_source':     'customfield_24344',
    'cand_source_other': 'customfield_25662',
    # ── REC-only (mobile) ──
    'rec_seniority':   'customfield_22876',   # Intern/Junior/Middle/Senior/Lead/Expert
    'rec_reason':      'customfield_22877',   # Replacement / Extention / Consultation
    'rec_team':        'customfield_23547',   # cascading Department / Sub-team
    'rec_num_spec':    'customfield_25663',   # RA: Number of specialists needed
    'rec_end_date':    'customfield_11232',   # End date (Open Position + RA)
    # ── WRP-only (web) ──
    'wrp_grade':       'customfield_13941',   # Trainee/Junior/Middle/Senior
    'wrp_reason':      'customfield_13936',   # team increase / replacement
    'wrp_team':        'customfield_13937',   # Team Web (select)
    'wrp_subteam':     'customfield_13938',   # Subteam (select)
}

# RA extra fields (REC consultant track — kept for parity with mobile dashboard)
RA_EXTRA_FIELDS = [
    'customfield_25664', 'customfield_25665', 'customfield_25666',
    'customfield_25667', 'customfield_25668', 'customfield_25669',
    'customfield_25670', 'customfield_25671', 'customfield_25672',
    'customfield_25673', 'customfield_25674',
]

BASE_FIELDS = ['summary', 'status', 'priority', 'issuetype', 'created',
               'parent', 'assignee', 'resolutiondate']
ALL_FIELDS = BASE_FIELDS + list(F.values()) + RA_EXTRA_FIELDS

# Board configuration: which issuetypes map to which dashboard kind, and how
# "active" vs "closed" are defined per board.
BOARDS = {
    'mobile': {
        'project': 'REC',
        'kinds': {
            'Open position': 'op',
            'Vacancy sub-task': 'op_sub',
            'Recruitment Assignment': 'ra',
            'Recruitment Assignment sub-task': 'ra_sub',
        },
        # Consistent definition across boards (see NOTE below):
        # active = In progress (not Done, not Plan — planned roles excluded).
        # closed = successfully filled → Hired (or Done for consulting calls).
        # Canceled (category Done, status "Canceled") is neither active nor closed.
        'active_jql':   'statusCategory != Done AND status not in (Plan, "On hold")',
        'closed_jql':   'status in (Hired, Done)',
        'canceled_jql': 'status = Canceled',
        'terminal':     'Hired',   # success status → close-date fallback via changelog
    },
    'web': {
        'project': 'WRP',
        'kinds': {
            'Vacancy': 'op',
            'Consultant': 'ra',
        },
        # WRP terminal statuses (category Done): "Closed" (filled) + "Canceled".
        # active = In progress (Plan excluded); closed = "Closed" only (Canceled excluded).
        'active_jql':   'statusCategory != Done AND status not in (Plan, "On hold")',
        'closed_jql':   'status = Closed',
        'canceled_jql': 'status = Canceled',
        'terminal':     'Closed',  # success status → close-date fallback via changelog
    },
}

# NOTE on role status semantics (calibrate with Yaroslava if needed):
#   REC statuses: Hired / Canceled (both Done cat) · Plan (To Do) · In progress.
#   WRP statuses: Closed / Canceled (both Done cat) · Plan (To Do) · In progress.
#   "Active" = In progress only (planned "Plan" roles are EXCLUDED per Yaroslava,
#   2026-07). "Closed" counts only successful fills; "Canceled" is excluded from
#   both buckets.

UA_MONTHS = ['січня', 'лютого', 'березня', 'квітня', 'травня', 'червня',
             'липня', 'серпня', 'вересня', 'жовтня', 'листопада', 'грудня']


# ── Value mappers (normalize web → mobile taxonomy) ─────────
def map_reason(raw, kind):
    """Unify Reason across boards. Consultant/RA items are always Consultation.

    Output uses the LEGACY spelling 'Extention' expected by the render layer
    (_reasonColors / .reason-extention CSS key)."""
    if kind in ('ra', 'ra_sub'):
        return 'Consultation'
    if not raw:
        return None
    r = raw.strip().lower()
    return {
        'extention':    'Extention',   # REC canonical (misspelled by design)
        'extension':    'Extention',
        'team increase': 'Extention',  # WRP → Extension bucket
        'replacement':  'Replacement',
        'consultation': 'Consultation',
    }.get(r, raw)


def map_seniority(raw):
    """WRP 'Trainee' → 'Intern' (REC taxonomy). Everything else passes through."""
    if not raw:
        return None
    return {'Trainee': 'Intern'}.get(raw, raw)


def map_employee_status(raw, kind):
    """Staff / Non-staff kept as-is. REC-only 'Expert' kept. Consultant/RA with no
    explicit status fall into the Consulting bucket downstream via kind."""
    return raw  # passthrough; Consulting split is derived from kind in the report


# ── Auth ────────────────────────────────────────────────────
def get_auth_header():
    email = os.environ.get('JIRA_EMAIL')
    token = os.environ.get('JIRA_API_TOKEN')
    if not email or not token:
        sys.exit("ERROR: JIRA_EMAIL and JIRA_API_TOKEN env vars are required")
    return 'Basic ' + base64.b64encode(f"{email}:{token}".encode()).decode()


HEADERS = {'Accept': 'application/json', 'Content-Type': 'application/json'}


# ── Jira API ────────────────────────────────────────────────
def jira_search(jql, fields, max_results=100):
    headers = dict(HEADERS)
    headers['Authorization'] = get_auth_header()
    url = f"{JIRA_HOST}/rest/api/3/search/jql"
    all_issues = []
    next_token = None
    while True:
        body = {'jql': jql, 'fields': fields, 'maxResults': max_results}
        if next_token:
            body['nextPageToken'] = next_token
        req = Request(url, data=json.dumps(body).encode('utf-8'),
                      headers=headers, method='POST')
        try:
            with urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
        except HTTPError as e:
            err = e.read().decode('utf-8', errors='replace')
            sys.exit(f"Jira API error {e.code}: {err[:500]}")
        all_issues.extend(data.get('issues', []))
        next_token = data.get('nextPageToken')
        if data.get('isLast', not next_token) or not next_token:
            break
    return all_issues


def fetch_terminal_transition_date(issue_key, target_status):
    """YYYY-MM-DD of the most recent transition INTO `target_status`.

    Used as a close-date fallback: 'Hired' for REC (mobile), 'Closed' for WRP
    (web). WRP items have neither Factual close date nor resolutiondate set, so
    without this the render layer has no close date at all for web (all 52 web
    closed roles would vanish under any period filter)."""
    headers = dict(HEADERS)
    headers['Authorization'] = get_auth_header()
    base = f"{JIRA_HOST}/rest/api/3/issue/{issue_key}/changelog"
    latest_date, start_at = None, 0
    while True:
        url = base + '?' + urlencode({'startAt': start_at, 'maxResults': 100})
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except HTTPError as e:
            print(f"  ⚠ changelog fetch failed for {issue_key}: HTTP {e.code}")
            return None
        for entry in data.get('values', []):
            for item in entry.get('items', []):
                if item.get('field') == 'status' and item.get('toString') == target_status:
                    d = (entry.get('created') or '')[:10]
                    if d and (latest_date is None or d > latest_date):
                        latest_date = d
        got = len(data.get('values', []))
        if got == 0 or data.get('isLast', True):
            break
        start_at += got
        if start_at >= data.get('total', start_at):
            break
    return latest_date


# ── Field extractors ────────────────────────────────────────
def get_user(field):
    if isinstance(field, list) and field:
        return field[0].get('displayName')
    if isinstance(field, dict):
        return field.get('displayName')
    return None


def get_option(field):
    if isinstance(field, list) and field:
        return field[0].get('value')
    if isinstance(field, dict):
        return field.get('value')
    return None


def get_cascading(field):
    if not field:
        return (None, None)
    return (field.get('value'), (field.get('child') or {}).get('value'))


def get_date(s):
    if not s:
        return None
    return s[:10] if len(s) >= 10 else s


# ── Board-agnostic department resolver ──────────────────────
def resolve_dept(source, fld):
    """Return (dept, subteam) normalized. Mobile uses cascading Team&subteams;
    web uses Team Web + Subteam. Source-suffix disambiguation (e.g. "Engineering
    (Web)") is applied in the report layer, not here — we keep raw dept + source."""
    if source == 'mobile':
        return get_cascading(fld.get(F['rec_team']))
    # web
    return (get_option(fld.get(F['wrp_team'])),
            get_option(fld.get(F['wrp_subteam'])))


def resolve_seniority(source, fld):
    if source == 'mobile':
        return map_seniority(get_option(fld.get(F['rec_seniority'])))
    return map_seniority(get_option(fld.get(F['wrp_grade'])))


def resolve_reason(source, fld, kind):
    raw = get_option(fld.get(F['rec_reason'] if source == 'mobile' else F['wrp_reason']))
    return map_reason(raw, kind)


def resolve_hires(fld, kind):
    """RA uses num_specialists on REC; everything else num_hires. Default 1."""
    if kind in ('ra', 'ra_sub'):
        return fld.get(F['rec_num_spec']) or fld.get(F['num_hires']) or 1
    return fld.get(F['num_hires']) or 1


# ── JS literal serializer ──────────────────────────────────
def js_value(v):
    if v is None:
        return 'null'
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)
    if isinstance(v, str):
        return '"' + v.replace('\\', '\\\\').replace('"', '\\"') \
                     .replace('\n', '\\n').replace('\r', '') + '"'
    if isinstance(v, list):
        return '[' + ','.join(js_value(x) for x in v) + ']'
    if isinstance(v, dict):
        parts = []
        for k, val in v.items():
            if re.match(r'^[A-Za-z_$][A-Za-z0-9_$]*$', str(k)):
                parts.append(f'{k}:{js_value(val)}')
            else:
                parts.append(f'"{k}":{js_value(val)}')
        return '{' + ','.join(parts) + '}'
    return 'null'


def js_array_decl(name, items, indent='  '):
    if not items:
        return f'const {name}=[];'
    body = ',\n'.join(indent + js_value(it) for it in items)
    return f'const {name}=[\n{body},\n];'


def js_object_decl(name, mapping, indent='  '):
    if not mapping:
        return f'const {name}={{}};'
    parts = [f'{indent}"{k}":{js_value(v)}' for k, v in mapping.items()]
    return f'const {name}={{\n' + ',\n'.join(parts) + '\n};'


# ── Builders ────────────────────────────────────────────────
def fetch_board(source):
    """Return dict with raw issue lists for one board."""
    cfg = BOARDS[source]
    proj = cfg['project']
    kinds = cfg['kinds']
    itypes = '","'.join(kinds.keys())
    itypes_clause = f'issuetype in ("{itypes}")'

    print(f"→ [{source}/{proj}] fetching active ({cfg['active_jql']})...")
    active = jira_search(
        f'project = {proj} AND {itypes_clause} AND {cfg["active_jql"]} ORDER BY key DESC',
        ALL_FIELDS)
    print(f"  got {len(active)} active")

    print(f"→ [{source}/{proj}] fetching closed ({cfg['closed_jql']})...")
    closed = jira_search(
        f'project = {proj} AND {itypes_clause} AND {cfg["closed_jql"]} ORDER BY key DESC',
        ALL_FIELDS)
    print(f"  got {len(closed)} closed")

    print(f"→ [{source}/{proj}] fetching canceled ({cfg['canceled_jql']})...")
    canceled = jira_search(
        f'project = {proj} AND {itypes_clause} AND {cfg["canceled_jql"]} ORDER BY key DESC',
        ALL_FIELDS)
    print(f"  got {len(canceled)} canceled")

    return {'source': source, 'active': active, 'closed': closed, 'canceled': canceled}


def item_common(source, issue, kind, status_name):
    """Build the shared item dict used by both active + closed arrays."""
    fld = issue['fields']
    dept, sb = resolve_dept(source, fld)
    typ = 'subtask' if kind in ('op_sub', 'ra_sub') else 'position'
    parent = fld.get('parent')
    item = {
        'key': issue['key'],
        's': fld.get('summary'),
        'source': source,
        'type': typ,
        'kind': kind,
        'st': status_name,
        'pr': (fld.get('priority') or {}).get('name'),
        'sn': resolve_seniority(source, fld),
        'r': resolve_reason(source, fld, kind),
        'es': map_employee_status(get_option(fld.get(F['employee_status'])), kind),
        'rec': get_user(fld.get(F['recruiter'])),
        'src': get_user(fld.get(F['sourcer'])),
        'so': fld.get(F['hiring_manager']),
        'sd': fld.get(F['start_date']),
        'ed': fld.get(F['rec_end_date']),
        'fcd': fld.get(F['fcd']),
        'fcd_c': fld.get(F['fcd_contact']),
        'rd': get_date(fld.get('resolutiondate')),  # fallback close date (see closeDate())
        'cs': get_option(fld.get(F['cand_source'])),
        'cs_other': fld.get(F['cand_source_other']) or None,
        'h': resolve_hires(fld, kind),
        't': dept,
        'sb': sb,
        'cr': get_date(fld.get('created')),
    }
    if parent:
        item['pk'] = parent.get('key')
    return item


def build_data():
    boards = [fetch_board('mobile'), fetch_board('web')]

    # Close-date fallback via changelog: date of transition INTO each board's
    # success status (REC → 'Hired', WRP → 'Closed'). WRP carries no Factual close
    # date and no resolutiondate, so this is the ONLY close date web closed roles
    # get — without it all web closed roles vanish under any period filter.
    close_transition = {}
    for b in boards:
        tgt = BOARDS[b['source']]['terminal']
        keys = [i['key'] for i in b['closed']
                if (i['fields'].get('status') or {}).get('name') == tgt]
        print(f"→ [{b['source']}] fetching changelog close-date for {len(keys)} '{tgt}' items...")
        for k in keys:
            close_transition[k] = fetch_terminal_transition_date(k, tgt)

    OP, RA, CV, CX = [], [], [], []
    SD, SN, RECR, SRCR = {}, {}, {}, {}

    for b in boards:
        source = b['source']
        kinds = BOARDS[source]['kinds']

        # ── active ──
        for issue in b['active']:
            fld = issue['fields']
            itype = (fld.get('issuetype') or {}).get('name')
            kind = kinds.get(itype, 'op')
            status_name = (fld.get('status') or {}).get('name')
            it = item_common(source, issue, kind, status_name)
            (RA if kind in ('ra', 'ra_sub') else OP).append(it)
            # lookups
            key = it['key']
            SD[key] = it['sd']
            if it['sn']:
                SN[key] = it['sn']
            if it['rec']:
                RECR[key] = it['rec']
            if it['src']:
                SRCR[key] = it['src']

        # ── closed ──
        for issue in b['closed']:
            fld = issue['fields']
            itype = (fld.get('issuetype') or {}).get('name')
            kind = kinds.get(itype, 'op')
            status_name = (fld.get('status') or {}).get('name')
            it = item_common(source, issue, kind, status_name)
            it['hd'] = close_transition.get(it['key'])  # transition→terminal (both boards)
            CV.append(it)

        # ── canceled (excluded from active + closed; shown only in Canceled KPI) ──
        for issue in b.get('canceled', []):
            fld = issue['fields']
            itype = (fld.get('issuetype') or {}).get('name')
            kind = kinds.get(itype, 'op')
            status_name = (fld.get('status') or {}).get('name')
            it = item_common(source, issue, kind, status_name)
            CX.append(it)

    return {
        'OP': OP, 'RA': RA, 'CV': CV, 'CX': CX,
        'SD': SD, 'SN': SN, 'RECR': RECR, 'SRCR': SRCR,
    }


# ── HTML rewriting ──────────────────────────────────────────
def replace_marker_block(text, start_re, end_re, new_inner):
    pattern = re.compile('(' + start_re + ')' + r'.*?' + '(' + end_re + ')', re.DOTALL)
    if not pattern.search(text):
        sys.exit(f"Marker pair not found in HTML: {start_re} … {end_re}")
    return pattern.sub(lambda m: m.group(1) + '\n' + new_inner + '\n' + m.group(2),
                       text, count=1)


def rewrite_html(data):
    with open(HTML_PATH, 'r', encoding='utf-8') as f:
        html = f.read()

    main_block = '\n'.join([
        '// Combined mobile(REC)+web(WRP) active roles. Each item: source, kind, r(reason),',
        '// sn(seniority), es(employee status), t/sb(dept), h(hires). See update_recruitment_analytics.py',
        js_array_decl('OP', data['OP']),
        '// Consultant track (REC Recruitment Assignment + WRP Consultant), active',
        js_array_decl('RA', data['RA']),
        '// Start dates lookup',
        js_object_decl('SD', data['SD']),
        '// Seniority lookup',
        js_object_decl('SN', data['SN']),
        '// Recruiter lookup',
        js_object_decl('RECR', data['RECR']),
        '// Sourcer lookup',
        js_object_decl('SRCR', data['SRCR']),
    ])
    html = replace_marker_block(
        html, r'// <<<AUTO_DATA_START>>>[^\n]*', r'// <<<AUTO_DATA_END>>>', main_block)

    cv_block = '\n'.join([
        '// Combined closed roles (mobile Hired+Done, web Closed). Close date = fcd (Factual',
        '// close date) preferred, else hd (transition into Hired/Closed via changelog), else rd.',
        js_array_decl('CV', data['CV']),
        '// Canceled roles (REC Canceled + WRP Canceled) — excluded from active & closed,',
        '// surfaced only in the Canceled KPI.',
        js_array_decl('CX', data['CX']),
    ])
    html = replace_marker_block(
        html, r'// <<<AUTO_CV_START>>>[^\n]*', r'// <<<AUTO_CV_END>>>', cv_block)

    now_kyiv = datetime.now(timezone(timedelta(hours=3)))
    date_str = (f"Дані: {now_kyiv.day} {UA_MONTHS[now_kyiv.month - 1]} {now_kyiv.year}, "
                f"оновлено {now_kyiv:%H:%M} (Kyiv)")
    html = re.sub(
        r'<!--<<<AUTO_DATE_START>>>-->.*?<!--<<<AUTO_DATE_END>>>-->',
        f'<!--<<<AUTO_DATE_START>>>-->{date_str}<!--<<<AUTO_DATE_END>>>-->',
        html, flags=re.DOTALL, count=1)

    with open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"✓ HTML rewritten: {HTML_PATH}")


# ── Main ────────────────────────────────────────────────────
def main():
    print("Updating Recruitment Analytics Report (REC + WRP)")
    data = build_data()
    n_mob = sum(1 for x in data['OP'] + data['RA'] if x['source'] == 'mobile')
    n_web = sum(1 for x in data['OP'] + data['RA'] if x['source'] == 'web')
    print("\nSummary (active):")
    print(f"  OP  (vacancies):        {len(data['OP'])}")
    print(f"  RA  (consultant track): {len(data['RA'])}")
    print(f"  CV  (closed):           {len(data['CV'])}")
    print(f"  CX  (canceled):         {len(data['CX'])}")
    print(f"  by source — mobile:{n_mob}  web:{n_web}")
    print(f"  hires sum (active):     {sum(x['h'] for x in data['OP'] + data['RA'])}")
    rewrite_html(data)


if __name__ == '__main__':
    main()
