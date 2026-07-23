# Recruitment Analytics Report (public mirror)

Password-gated public mirror of the Recruitment Analytics Report (Mobile REC + Web WRP),
for sharing with viewers who don't have access to the private source repo.

- **Live:** <https://olenapizniak.github.io/rec-analytics-report/reports/Recruitment_Analytics_Report.html>
- **Access:** open the link and enter the password (soft client-side gate — the embedded
  data is still present in page source, so treat this as "not-indexed / casual-access"
  protection, not confidential-grade security).
- **Source of truth:** private repo `betterme-sandbox/rec-recruitment-dashboard`.

## Auto-update
`.github/workflows/update-recruitment-analytics.yml` runs `scripts/update_recruitment_analytics.py`
twice a day (05:10 / 12:10 UTC) and rewrites the data blocks in the HTML between the
`AUTO_*` markers. The password gate lives in `<head>` (outside those markers), so refreshes
preserve it.

Requires repo secrets `JIRA_EMAIL` and `JIRA_API_TOKEN` (Settings → Secrets and variables → Actions).
