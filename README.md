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

The viewer preserves the last good edition on update failure, checks for new JSON every five minutes, and displays a stale warning for an old issue date, an expired capture, or expired story/weather provenance. Publication also rechecks timestamp age and rejects undeclared nested fields. A pre-6 a.m. manual edition does not suppress the morning refresh. Morning generation is attempted from 6:17 a.m. Eastern ahead of the 7:00 a.m. TV target, subject to GitHub delays; it is not a guaranteed exact-time delivery service. No email delivery is enabled by this repository.

Required developer browser verification: run **both** `python verify_snapshot_cloud.py` and `python verify_viewer.py` (requires Playwright and installed Edge; not a production dependency). The Snapshot suite tests the default headline board, headline coverage, TV viewport bounds, ticker, mobile/reduced motion, and failed-refresh retention. The Story suite tests detailed slides at three TV sizes, pause/navigation, stale dates, update failure, and untrusted text injection. Neither suite alone is the complete release check.

## TV access and approved viewer

The public viewer is at `https://ndooley-11.github.io/morning-newsletter/`. It is hosted by GitHub Pages, not by a browser or server running on the owner's computer. The TV still needs internet access and a suitable browser or streaming-device display app.

The approved interface defaults to a newspaper-style headline snapshot with a persistent bottom news ticker. It offers a detailed Story view, previous/next controls, pause and fullscreen where the browser supports it. Weather stays at the top. The viewer checks for updated public JSON every five minutes and retains the previous valid edition when a refresh fails. Its date/freshness status remains visible; scheduled collection is a separate cloud workflow, not continuous live reporting.

The free collector does not currently provide stock price quotes or licensed editorial photos. The ticker must say **Quote unavailable**, not show old sample prices as current. General priority headlines can still scroll. Market research, private mail and the richer original sample are not silently substituted into the cloud edition.

On a TV with a web browser:

1. Open the public URL above and bookmark it.
2. Select **Fullscreen** if available. Otherwise use the browser's full-screen/display setting and set the TV picture sizing to fit without overscan.
3. Use **Pause** for a stationary morning board, **Previous/Next** to read additional headline pages, and **Story view** for source details.
4. Reopen the bookmark after the TV starts. Automatic app launch and sleep/power behavior depend on the actual TV or streaming device and have not been verified on physical hardware.

If the device has no suitable browser, choose a supported browser-equipped streaming device or cast the public page from another device. Hardware-specific installation needs the TV/streaming-device model; browser emulation is not physical-TV acceptance.

The layout release changes only reviewed viewer files, developer checks and documentation. Email delivery, DNS and the portfolio site remain unchanged. To roll back a layout release, revert its specific commit on `main` and push that revert; do not reset history or replace the latest public content with the old design sample. The existing workflow will generate/validate and redeploy, retaining the previous successful Pages deployment if it fails.

## Repository deployment setup

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
17 10,11 * * *
```

GitHub evaluates cron in UTC. The two daily slots help cover the U.S. daylight-saving transition: 10:17 UTC is 6:17 a.m. Eastern during daylight time; 11:17 UTC is 6:17 a.m. during standard time. The generator's Eastern 06:00–10:59 gate skips the early winter slot and a validated same-day morning capture suppresses the duplicate summer slot. Scheduled workflows can be delayed during high load. GitHub also automatically disables scheduled workflows in public repositories after 60 days with no repository activity; re-enable the workflow in the Actions UI or make an appropriate repository update.

See `TV-MORNING-SETUP.md` for the separate 7 a.m. TV wake and browser-start acceptance test. A cloud content schedule does not turn a television on.

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
