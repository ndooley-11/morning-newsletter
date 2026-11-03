# Morning Newsletter Cloud

Static GitHub Pages deployment for the Morning Newsletter. The public site is built only from `site/`, and the daily workflow stores the last successfully generated `site/content.json` in the repository so a later skipped run can reuse it.

## Readiness states

- **Prepared locally:** this repository contains the workflow and operating instructions. This does not prove GitHub configuration, a cloud run, a scheduled run, or display hardware.
- **Manual cloud run proven:** after the public repository exists, Pages is configured, and a successful `workflow_dispatch` run deploys and verifies the expected edition ID.
- **Scheduled operation proven:** only after a real `schedule` event completes successfully in GitHub Actions. A manual run is not proof that cron scheduling fired.
- **Display hardware:** no television, browser kiosk, refresh behavior, or other physical display hardware is verified by this repository.

## Public content and limitations

This free cloud edition is a **source-title digest, not the original AI-researched full newsletter**. It collects at most two current titles per configured RSS/Atom feed, links to the publisher, and shows publication/capture timestamps. It does not copy article bodies. NWS supplies forecasts for Savannah and Hinsdale. Stories older than 48 hours, undated stories, and forecasts older than 24 hours are rejected.

BBC, ESPN, NASA, Dezeen, and Budget Bytes are configured. A source can fail or have no current stories; those gaps are disclosed. Markets, separate short/long watchlist coverage, betting, private inbox/calendar briefing, new comics, photos, detailed recipes and customized favorite-team reporting are **not supplied by this initial free generator**. No picks, wagers, trades, or fabricated substitute reporting are generated. The public edition must have current stories in at least two desks and weather for at least one location before publishing.

The viewer preserves the last good edition on update failure, checks for new JSON every five minutes, and displays a stale warning for an old issue date, an expired capture, or expired story/weather provenance. Publication also rechecks timestamp age and rejects undeclared nested fields. A pre-7 a.m. manual edition does not suppress the morning refresh. Morning generation is attempted from 7:17 a.m. Eastern ahead of the 8:00 a.m. target, subject to GitHub delays; it is not a guaranteed 8:00 delivery service. No email delivery is enabled by this repository.

Developer browser verification: `python verify_viewer.py` (requires Playwright and installed Edge; not a production dependency). It tests every slide at three TV sizes, mobile/reduced motion, pause/navigation, stale dates, update failure, and untrusted text injection.

## Repository and Pages setup

The intended public repository is `ndooley-11/morning-newsletter`. Keep the private `MorningNewsletter` project separate; do not copy its Git history, credentials, private data, or unrelated files into this repository.

After creating the public repository:

1. Put the generator and tests at the repository root, including `generate_edition.py`.
2. Put the complete static site in `site/`: `index.html`, `app.js`, `styles.css`, and a valid `content.json`.
3. In **Settings → Pages → Build and deployment**, choose **GitHub Actions** as the source.
4. Push to `main`, then run **Actions → Daily newsletter → Run workflow** with **Generate fresh content** enabled.
5. Confirm both jobs succeed and the `Verify published edition` step reports the same `edition.id` as `site/content.json`.
6. Enable GitHub's native Actions failure notifications for the maintainers who should respond. No third-party monitor, email token, or paid alerting service is required.

The workflow has no `pull_request` trigger. It runs only from `main` pushes, manual dispatches, and the schedule. Every third-party action is pinned to a full commit SHA.

## Daily operation

The workflow uses a standard GitHub-hosted `ubuntu-latest` runner and Python 3.13. It:

1. Checks out the repository, including the stored current content.
2. Compiles Python sources and runs `python -m unittest discover -v` before generation.
3. Runs one generation mode:
   - scheduled event: `python generate_edition.py --output site/content.json --scheduled`
   - manual dispatch with regeneration enabled, or a push to `main`: `python generate_edition.py --output site/content.json --force`
   - manual dispatch with regeneration disabled: retain and strongly validate the stored `site/content.json`
4. Requires the four site files and a non-empty `edition.id`.
5. Rejects changes outside `site/content.json`, stages only `site/content.json`, and commits changed content as `github-actions[bot]` with `[skip ci]`.
6. Uploads only `site/`, deploys it to Pages, then polls the published `content.json` up to 10 times at 10-second intervals. The expected edition ID is used only as a cache-busting query value during verification.

If scheduled generation decides that no new edition is needed, deploying the unchanged stored content is acceptable. If tests, generation, validation, commit/push, artifact upload, or deployment fails, the deploy job does not publish a new artifact; the last successful Pages deployment remains available.

### Schedule behavior

The cron expression is:

```text
17 11,12 * * *
```

GitHub evaluates cron in UTC. The two daily slots help cover the U.S. daylight-saving transition; `--scheduled` must make duplicate same-day runs safe and may skip the second run. Scheduled workflows can be delayed during high load. GitHub also automatically disables scheduled workflows in public repositories after 60 days with no repository activity; re-enable the workflow in the Actions UI or make an appropriate repository update.

## Cost and dependencies

**Operating cost: $0.** This design uses a public GitHub repository, standard GitHub-hosted Actions runners, and GitHub Pages. It requires no paid services, paid APIs, hosted models, API keys, or repository secrets. Usage remains subject to GitHub's public-repository and acceptable-use policies.

The workflow intentionally does not install dependencies. The committed generator and tests must run with the Python 3.13 standard library and must not depend on paid networks, APIs, or models.

## Rollback

The repository copy of `site/content.json` is the last successful generated content and is the fallback for the next run.

For a bad code or content change:

1. Revert the bad commit on `main` with `git revert <bad-commit>` and push the revert.
2. If the reverted repository already contains the desired good `site/content.json`, manually run **Daily newsletter** with **Generate fresh content** disabled. The workflow validates and deploys that stored edition without regenerating it.
3. Verify the workflow's published edition check succeeds and inspect the public page.

If generation itself fails, do not replace the stored good content. Fix or revert the generator, then run manually with regeneration enabled. A failed run does not replace the last successful Pages deployment.

## Monitoring and incident checks

Use GitHub's native notifications and the repository's **Actions** page. For a failure, inspect in order:

1. unit-test output;
2. generator output and whether scheduled mode intentionally skipped;
3. content validation and unexpected-file checks;
4. the explicit `site/content.json` commit/push;
5. Pages artifact upload and deployment;
6. published edition-ID verification.

No paid third-party monitoring or email credential is part of this design.

## Future custom domain

Do not add a `CNAME` file initially. The future target is `newsletter.nicolasdooley.com`.

After GitHub domain verification and separate approval for the DNS change:

1. Configure the custom domain in the repository's Pages settings.
2. Create the DNS CNAME `newsletter.nicolasdooley.com` pointing to `ndooley-11.github.io`.
3. Wait for GitHub's DNS check and TLS certificate, then enable HTTPS enforcement.
4. Verify both the custom hostname and the published edition ID.

Leave the existing ChatGPT portfolio apex and `www` DNS records unchanged.
