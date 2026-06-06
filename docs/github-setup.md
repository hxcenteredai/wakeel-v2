# GitHub repository (Acceptance criterion #6)

Per the client's instruction (Jun 1), v1 is **GitHub-only — no GitLab**. Milestone 1
criterion #6 is therefore: *"GitHub repo current with all delivered code."*

- **Repository:** `https://github.com/hxcenteredai/wakeel`
- Developer pushes directly to this repo (collaborator access required).
- The repo's pre-existing scaffold is **reference only**; this codebase is the
  source of truth.

## Pushing the code

```bash
git init                       # if not already a repo
git add .
git commit -m "feat: build-mode foundation (Milestone 1)"
git branch -M main
git remote add origin https://github.com/hxcenteredai/wakeel.git
git push -u origin main
```

If `origin` already exists, use `git remote set-url origin <url>`.

## Authentication

Use a GitHub Personal Access Token (scope `repo`) or SSH. Example with a token:

```bash
git remote set-url origin https://<username>:<token>@github.com/hxcenteredai/wakeel.git
git push -u origin main
```

## Acceptance note

Criterion #6 is met when the GitHub `main` branch contains the complete, current
codebase (app, tests, examples, logs, docs, Dockerfile, requirements). No secrets
are committed (`.env` is gitignored; only `.env.example` ships).
