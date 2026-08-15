# Phase F Checkpoint: Research Scale-Up

Date: 2026-08-14

Status: complete and awaiting human approval. Phase G has not started.

## Scope and integrity

- Phase 2 research ran for all 22 eligible cold prospects that were missing artifacts. All 22 runs completed without an exception.
- The existing Balyasny artifact was retained.
- Citadel and Houlihan Lokey remained excluded from the automated crawler because their sites are bot-blocked. Their artifacts were written manually from direct, official firm pages and passed the same `research/schema.json` validation used by `research/fetch.py`.
- All 25 cold-prospect artifacts passed schema, firm, slug, domain or identity-checked canonical-domain, region, relationship, source-namespace, and hook-source consistency checks.
- Three target domains redirect to identity-checked official canonical domains: Bentall GreenOak to `bgo.com`, The Jordan Company to `tjclp.com`, and Makena Capital Management to `makenacap.com`.
- Research and drafting remained separate. No cold email drafts were generated during Phase F.
- No Hunter call was made. `contacts/hunter_usage.csv` remains 9 lines, 1,047 bytes, with SHA-256 `32fe158de2c3d6c736c19afeba46a2a521e972b3b932008438abc1032cf31b05` and modification time `1786643117`, exactly matching the pre-run baseline.
- `config/blk_facts.json` remained read only and unchanged.
- All 394 tests passed after the artifact audit.

## Confidence by cold prospect

The automated confidence score counts distinct non-landing page categories with citable evidence. A low-confidence artifact is shown as `MANUAL-QUEUE` because the existing acceptance path prohibits it from advancing to drafting. Every non-queued artifact below has three sourced alignment hooks.

| Firm | Owner | Checkpoint tier | Basis |
| --- | --- | --- | --- |
| General Atlantic | jamari | MEDIUM | 2 evidence categories |
| Summit Partners | jamari | HIGH | 3 evidence categories |
| TA Associates | jamari | MEDIUM | 2 evidence categories |
| Insight Partners | jamari | MEDIUM | 2 evidence categories |
| Ares Management | jamari | MEDIUM | 2 evidence categories |
| Blue Owl Capital | jamari | HIGH | 3 evidence categories |
| HPS Investment Partners | jamari | MEDIUM | 2 evidence categories |
| Citadel | jamari | HIGH | Manual official-source artifact; 3 evidence categories |
| Millennium Management | jamari | MEDIUM | 2 evidence categories |
| Adams Street Partners | jamari | HIGH | 3 evidence categories |
| Audax Group | jamari | MANUAL-QUEUE | Artifact confidence LOW; only landing-page evidence qualified |
| Bentall GreenOak | jamari | MEDIUM | 2 evidence categories |
| Capstone Investment Advisors | jamari | MEDIUM | 2 evidence categories |
| Balyasny Asset Management | jamari | HIGH | Existing validated artifact; 3 evidence categories |
| Coatue Management | jamari | MANUAL-QUEUE | Artifact confidence LOW; only 1 non-landing evidence category qualified |
| The Jordan Company | jamari | MANUAL-QUEUE | Artifact confidence LOW; only 1 non-landing evidence category qualified |
| Alger | fola | MANUAL-QUEUE | Artifact confidence LOW; zero sourced alignment hooks |
| ClearBridge Investments | fola | MANUAL-QUEUE | Artifact confidence LOW; zero sourced alignment hooks |
| Makena Capital Management | fola | MANUAL-QUEUE | Artifact confidence LOW; only 1 non-landing evidence category qualified |
| Mondrian Investment Partners | fola | HIGH | 4 evidence categories |
| Houlihan Lokey | fola | MEDIUM | Manual official-source artifact; 2 evidence categories |
| Lincoln International | fola | MEDIUM | 2 evidence categories |
| Piper Sandler | fola | HIGH | 3 evidence categories |
| The Raine Group | fola | MANUAL-QUEUE | Artifact confidence LOW; hooks came only from the landing page, with zero qualifying non-landing categories |
| TD Bank | fola | MANUAL-QUEUE | Artifact confidence LOW; only 1 non-landing evidence category qualified |

Summary: 7 HIGH, 10 MEDIUM, and 8 MANUAL-QUEUE.

## Manual-queue reasons

The Phase F cold-prospect queue contains these eight firms. `review/manual_queue.csv` also retains one older non-cold Advent row from the prior research state; it is not part of this Phase F cold count.

1. **Audax Group:** Research confidence is low. The crawl found no qualifying non-landing evidence category, no campus or early-career program, no recruiting-timeline signal, no office evidence, no student-organization partnership, no relevant news, and no careers page.
2. **Coatue Management:** Research confidence is low. Only the news category qualified; the crawl found no campus or early-career program, recruiting-timeline signal, office evidence, student-organization partnership, careers page, or university-recruiting page.
3. **The Jordan Company:** Research confidence is low. Only the values and culture category qualified; the crawl found no campus or early-career program, recruiting-timeline signal, office evidence, student-organization partnership, relevant news, or careers page.
4. **Alger:** No alignment hook with a source URL was available. The crawl found no citable values theme, campus or early-career program, recruiting-timeline signal, office evidence, asset-class evidence, or student-organization partnership.
5. **ClearBridge Investments:** No alignment hook with a source URL was available. The crawl found no citable values theme, campus or early-career program, recruiting-timeline signal, office evidence, asset-class evidence, or student-organization partnership.
6. **Makena Capital Management:** Research confidence is low. Only the news category qualified; the crawl found no campus or early-career program, recruiting-timeline signal, office evidence, student-organization partnership, careers page, university-recruiting page, or values and culture page.
7. **The Raine Group:** Research confidence is low. Its hooks came only from the landing page, and the crawl found no campus or early-career program, recruiting-timeline signal, student-organization partnership, relevant news, careers page, or university-recruiting page.
8. **TD Bank:** Research confidence is low. Only the values and culture category qualified; the crawl found no campus or early-career program, recruiting-timeline signal, office evidence, asset-class evidence, student-organization partnership, or relevant news.

## Manual official-source artifacts

Citadel was reviewed from its official Programs and Events, Internships, Discover Citadel, and Our Culture pages. Houlihan Lokey was reviewed from its official Careers, U.S. Internships, and How to Apply pages. Each claim carries the direct official page URL and a supporting quote. No attempt was made to bypass either site's bot protection.

## Required gate

Phase G must not begin until a human creates `review/checkpoints/phaseF_APPROVED.md`.
