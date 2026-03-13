# BD4 Bug Intake

Central bug reporting for BD4. Bugs filed here are automatically triaged by AI and routed to the appropriate repository as sub-issues.

## How to report a bug

1. Go to [Issues > New Issue](../../issues/new/choose)
2. Fill out the bug report form
3. AI will automatically analyze and create sub-issues in the relevant repos

## How it works

- New issues get the `needs-triage` label
- A GitHub Action calls GPT-4o to classify which repo(s) the bug belongs to
- Sub-issues are created in `tbd-frontend`, `tbd-bff`, or `tbd-backend`
- The intake issue becomes the tracking parent with linked sub-issues
- Low-confidence triage is flagged for human review via Slack

## Switching to Claude

To switch from GPT-4o to Claude for triage:
1. Add `ANTHROPIC_API_KEY` as a repository secret
2. Set repository variable `LLM_PROVIDER` to `anthropic`

## Repos

| Repo | Scope |
|------|-------|
| `tbd-frontend` | Flutter mobile app (iOS/Android) |
| `tbd-bff` | Next.js BFF middleware |
| `tbd-backend` | FastAPI API + Celery workers |
| `tbd-design` | Design specs and Figma references |
