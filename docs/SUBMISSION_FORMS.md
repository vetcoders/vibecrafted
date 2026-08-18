# Submission Forms

Prepared on 2026-04-11 for the current pre-release shape of 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.

Official submit/help surfaces below were re-checked on 2026-04-11 before this pack was refreshed.

## Positioning anchor

- Product name: 𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.
- Primary tagline: Release engine for AI-built software.
- Secondary tagline: Ship AI-built software without the vibe hangover.
- Core promise: Take the repo your AI agents already produced and force it through structural mapping, convergence loops, install truth, packaging, and launch-readiness checks until it is fit to ship.
- Website: https://vibecrafted.io/
- Repository: https://github.com/vetcoders/vibecrafted
- Docs: https://vibecrafted.io/en/quickstart/
- FAQ: https://vibecrafted.io/en/faq/
- Contact: info@vibecrafted.io

## Recommended launch order

0. awesome-agent-orchestrators
   Official submit surface: https://github.com/andyrewlee/awesome-agent-orchestrators (fork, add one line, open a PR)
   Why zeroth: it is the only entry on this list that costs nothing, waits on no
   editorial queue, and is read by exactly our audience. It is also the one
   directory where the namesake is already listed and we are not — see the
   directory note below. Do this before any paid or curated submission.

1. There's An AI For That
   Official submit surface: https://theresanaiforthat.com/launch/
   Why first: highest AI-directory traffic, native support for launches and developer tools.

2. Future Tools
   Official submit surface: https://futuretools.io/submit-a-tool
   Why second: curated by a known AI-tools audience and already friendly to GitHub-hosted tools.

3. Futurepedia
   Official submit surface: https://www.futurepedia.io/submit-tool
   Why third: strong reach with professionals and a cleaner business/adoption audience.

4. Toolify
   Official submit surface: https://www.toolify.ai/submit
   Why fourth: wide directory reach, SEO value, and explicit developer-tools taxonomy.

5. TopAI.tools
   Official submit surface: https://topai.tools/submit
   Why fifth: lower-friction directory with fast editorial review and solid discovery value.

6. Uneed
   Official submit surface: https://www.uneed.best/submit-a-tool
   Launch guide: https://www.uneed.best/launch-guide
   Why sixth: softer launchpad than Product Hunt, useful for testing screenshots,
   copy, and founder response loops once the AI-directory listings are already live.

7. Product Hunt
   Posting access: https://help.producthunt.com/en/articles/481909-how-can-i-get-access-to-post
   Launch prep: https://www.producthunt.com/launch/before-launch
   Why seventh: not a directory, but the best single public launch spike once the landing page and demo are fully sharp.

## Marketplace fit from adjacent tools

These directories already surface tools that sit close to Vibecrafted's
territory, which is useful for positioning:

- There’s An AI For That lists Cursor at
  `https://theresanaiforthat.com/ai/cursor-ai/`.
- Future Tools currently lists both Cursor and Devin at
  `https://www.futuretools.io/tools/cursor` and
  `https://futuretools.io/tools/devin-ai`.
- Future Tools also lists Bolt.new at
  `https://www.futuretools.io/tools/bolt-new`, which sharpens the contrast
  between "AI builds the draft" and "Vibecrafted makes the draft shippable."
- Futurepedia surfaces Cognition AI / Devin at
  `https://www.futurepedia.io/tool/cognition-ai` and Build AI at
  `https://www.futurepedia.io/tool/build-ai`.
- Toolify currently carries Cursor and Windsurf at
  `https://www.toolify.ai/tool/cursor` and
  `https://www.toolify.ai/tool/windsurf`.
- TopAI.tools carries Windsurf at
  `https://topai.tools/t/windsurf-ai-ide-codeium`.

That means the positioning has to stay sharp:

- Adjacent tools help write code, generate apps, or act inside the IDE.
- Vibecrafted is the release layer after that draft exists.
- Pitch the product as "ship AI-built software" rather than "generate software
  with AI."

## Canonical short-form fields

- Name
  𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.

- Tagline
  Release engine for AI-built software.

- Alternate tagline
  Ship AI-built software without the vibe hangover.

- 160-char description
  Vibecrafted hardens AI-generated repos through structural mapping, convergence loops, install audits, and launch-ready packaging.

- 300-char description
  Vibecrafted is the release engine for AI-built software. It takes the repo your agents already produced and drives it through perception, verification, convergence loops, install truth, and launch-readiness work until the product is fit to ship.

- Category candidates
  Developer Tools
  AI Agents
  Coding Assistant
  DevOps
  Workflow Automation
  Release Engineering

- Keywords
  AI agent engineering, release engineering, developer tools, Claude, Codex, Gemini, multi-agent workflow, AI-native QA, launch readiness

- Pricing
  Free for personal use and startups. Enterprise licensing available.

- Platform
  macOS, Linux, Windows (WSL2)

- Guided install CTA
  macOS/Linux/WSL2 bootstrap: `curl -fsSL https://vibecrafted.io/install.sh | bash`.
  When a release attaches a DMG, macOS can instead open
  `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg` from the
  [latest release](https://github.com/vetcoders/vibecrafted/releases/latest)
  (none published yet).

- Direct install CTA
  Source checkout: `git clone` then `make install`. Maintainer staging:
  `make install-auto`.

## Canonical submission packet

Prepare these fields once, then reuse them across directories:

- Product name
  𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.

- Homepage URL
  https://vibecrafted.io/

- Product URL
  https://github.com/vetcoders/vibecrafted

- Primary CTA
  Guided install

- Primary CTA URL
  https://vibecrafted.io/

- Install method
  Bootstrap `install.sh` on macOS, Linux and WSL2; signed DMG on macOS when a
  release attaches one; `install.ps1` is a WSL2 handoff, not a native Windows
  installer.

- Best-fit audience
  Founders and software teams shipping AI-generated or AI-maintained repos into production.

- Problem statement
  AI can generate a repo faster than most teams can reality-check it. The result is a vibe hangover: code that looks finished until install, trust, or launch reality breaks it.

- Core outcome
  Move an AI-built repo from draft-shaped to shippable by forcing structural truth, convergence loops, install truth, docs, packaging, and launch readiness.

- Suggested assets
  Guided installer screenshot, command deck screenshot, marbles/convergence screenshot, quickstart page screenshot, one short walkthrough video.

## Canonical long-form answer

Use this for "What does it do?" text areas:

Vibecrafted starts where code generation stops. It takes the repo your AI agents already produced and forces it through structural mapping, convergence loops, install truth, packaging, and launch-readiness checks until the product is fit to ship. Instead of promising that AI will write your code, it promises something harder: the repo will be pushed toward production truth, not left in a demo-ready but release-fragile state.

## Kickoff guardrail

Do not launch any directory listing while the portal or deployment mirror still
shows the old direct-only install CTA or the older self-referential promise.
Every public surface should agree on:

- promise: `Release engine for AI-built software.`
- primary CTA: `curl -fsSL https://vibecrafted.io/install.sh | bash` on
  macOS, Linux and WSL2 (Windows: `wsl --install`, then that bootstrap)
- secondary CTA: signed `Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg` when
  a GitHub Release actually attaches one; until then say so
- source CTA: checkout and `make install` / `make install-auto`
- audience: founders and teams shipping AI-generated repos into production

## Directory-specific notes

### awesome-agent-orchestrators

- Submit URL: https://github.com/andyrewlee/awesome-agent-orchestrators
- Mechanism: a curated GitHub awesome-list, ~1.2k stars. Submission is a fork
  plus a pull request adding one line in the list's own `Name - sentence.`
  format. No fee, no editorial queue, no launch window.
- Why it is first: its topic tags are our exact audience — `claude-code`,
  `codex`, `gemini-cli`, `multi-agent-systems`, `parallel-agents`,
  `git-worktree`, `agent-orchestration`. Neighbouring entries (ClawTeam,
  CompanyHelm, Contrabass, vibe-kanban, constellagent, parallel-code) are the
  tools a stranger currently finds instead of us.
- **Positioning risk, measured 2026-08-18.** A near-identical namesake,
  `vibecraft.build`, is already listed here and ranks second for the category
  query while Vibecrafted returns nothing. The entry must therefore differentiate
  in its first clause, not its last: most neighbours orchestrate agents across
  worktrees, which is the crowded half of the category. Ours is the lifecycle and
  the settlement ledger — the part that says whether the work actually landed.
- Paste-ready line, in the list's format:

  ```text
  Vibecrafted - Lifecycle runtime that drives Claude, Codex, and Gemini through an eleven-stage read/write cadence on one shared checkout, settling every stage against a signed delivery ledger instead of an agent's own report.
  ```

- Prerequisite: land it **after** a GitHub Release actually carries an
  installable artifact. An awesome-list PR pointing at a repository whose latest
  release is four minors stale is the one version of this submission that can be
  rejected on merit.

### There's An AI For That

- Submit URL: https://theresanaiforthat.com/launch/
- Notes from the current page:
  The current launch page still frames TAAFT as a launch surface rather than a passive directory, and the official launch/get-featured flow still says listings are manually reviewed with an average 1-2 day turnaround.
  The current paid launch path still presents the listing as a one-time fee with refund on denial, and the get-featured page still says launching on TAAFT first can unlock a $300 PPC bonus.
  Their current copy also still leans hard on newsletter distribution and first-week launch traffic, which keeps this as the strongest first directory once the portal and install path are sharp.
- Suggested task/category tags:
  developer tools, coding, workflow automation, ai agents, release engineering
- Paste-ready emphasis:
  Launch the guided install path first and frame Vibecrafted as the missing layer between AI-generated code and something a stranger can trust.

### Future Tools

- Submit URL: https://futuretools.io/submit-a-tool
- FAQ surface: https://www.futuretools.io/faq
- Notes from the current page:
  The page still says "Matt will review it," so this is curated, not guaranteed.
  The FAQ surface still exposes the practical queue questions ("I submitted a tool", "When will my tool go live?", "It's been over a week"), so treat approval as editorial throughput, not instant self-serve publishing.
  Future Tools also positions itself as a media/discovery surface tied to 230,000+ subscribers, so submit early and treat this as a credibility surface rather than a same-day traffic spike.
- Suggested emphasis:
  founders, software teams, shipping AI-coded products, command deck, convergence loops
- Paste-ready emphasis:
  Keep the writeup founder-readable and concrete. This audience responds better to a crisp before/after story than to framework lore.

### Futurepedia

- Submit URL: https://www.futurepedia.io/submit-tool
- Notes from the current page:
  The submit page currently positions Futurepedia as a work-focused AI-tools surface for 400k+ proactive professionals per month and asks you to choose the listing option that fits your product.
  Their verified listing surface currently advertises a $497 one-time tier, published within 2 business days, with refund on denial, which reinforces that this audience expects business-readiness and clear value.
  Lead with release readiness and concrete workflow outcomes, not framework mythology.
- Suggested emphasis:
  move from AI draft to shippable product, structural truth, launch readiness, product surface completion
- Paste-ready emphasis:
  Lead with business readiness and production hardening. Treat this as a professional-work surface, not a hacker launch board.

### Toolify

- Submit URL: https://www.toolify.ai/submit
- Notes from the current page:
  Toolify's current submit page still frames the listing around visibility, trial rate, and paying users, and now foregrounds 8M+ page views, 5.1M+ monthly visits, and 700K+ subscribers.
  It also explicitly promises "Listing & Traffic forever" and pushes GPT submission, update tooling, and launch badges, so treat it as an SEO/distribution engine rather than a one-day launch board.
- Suggested tags:
  developer tools, ai agents, workflow automation, release engineering, test automation
- Paste-ready emphasis:
  Use strong keywords, pricing clarity, and the guided-install / direct-install split. Toolify leans heavily toward discoverability and conversion mechanics.

### TopAI.tools

- Submit URL: https://topai.tools/submit
- Notes from the current page:
  The current submit page says submissions are reviewed within 48 hours, full refunds are automated if the tool is not accepted, and one payment secures the listing indefinitely.
  It also emphasizes Quick Update / self-updates for claimed tools and 2M+ qualified monthly users, which makes it a strong SEO/discovery surface once the portal and install CTA are fully stable.
- Suggested emphasis:
  AI-generated repo hardening, release engineering, verification loops, install path, docs and trust surface
- Paste-ready emphasis:
  Stress that the product is AI-powered, functional now, and directly usable from the public install path.

### Product Hunt

- Posting access guide: https://help.producthunt.com/en/articles/481909-how-can-i-get-access-to-post
- Launch prep: https://www.producthunt.com/launch/before-launch
- Notes from the current docs:
  Product Hunt currently requires a personal account to post; company accounts cannot post. New accounts wait one week before posting unless the maker unlocks earlier access through the newsletter shortcut.
  The current before-launch guide still says to get familiar with the community first, fill out the maker profile, and invite your team/community early so they are ready to engage.
  Self-posting is normal; do not plan around paid hunters/promoters.
  Prepare the first maker comment in advance and only launch once the portal, guided install, and demo are all ready for real strangers.
- Submission package to prepare:
  5 screenshots
  1 short walkthrough video or interactive demo
  1 founder-first comment explaining the vibe hangover problem and why Vibecrafted exists
- Paste-ready emphasis:
  Present Vibecrafted as the release engine that comes after Cursor / Claude / Codex / Lovable have already produced the draft. Product Hunt needs a clear "why this exists" contrast more than a long feature list.

### Uneed

- Submit URL: https://www.uneed.best/submit-a-tool
- Launch guide: https://www.uneed.best/launch-guide
- Notes from the current pages:
  Uneed currently requires an account before you can submit a product, which is useful because launch-day edits stay self-serve afterward.
  Their launch guide currently says you can join the waiting line for free or pay to choose an exact launch date, and it recommends Tuesday through Thursday over weekends.
  This is a stronger "launch rehearsal" surface than a pure AI directory, so use it after the AI listings are live but before the all-in Product Hunt spike.
- Suggested emphasis:
  founder story, launch-ready onboarding, public install path, screenshots that explain the product in one glance
- Paste-ready emphasis:
  Treat Uneed like a launch-day page, not a metadata directory. Lead with the vibe hangover story, show the guided installer, and keep the product explanation plain-language.

## Proposed form presets

Use the default packet above, then bias each marketplace form this way:

- There’s An AI For That
  Lead with the guided installer as the human front door and category it under developer tools / coding / workflow automation.

- Future Tools
  Keep the description founder-readable: before AI draft, after Vibecrafted release pass.

- Futurepedia
  Emphasize business readiness, production hardening, and the move from draft-shaped repo to shippable product.

- Toolify
  Max out keyword clarity: AI agents, release engineering, workflow automation, install truth, launch readiness.

- TopAI.tools
  Focus on usefulness now: public install path, real docs, real verification loop, real release outcome.

- Uneed
  Treat it as a launchpad: lean on founder-readable pain, strong visuals, and the guided install screenshot rather than a taxonomy-heavy directory pitch.

- Product Hunt
  Lead with the vibe hangover story, show the guided install and command deck, and make the maker comment do the positioning work.

## Fast-fill operator sheet

Use these as the default copy/paste answers when a form asks for the common
launch fields:

| Field            | Paste-ready answer                                                                                                                                                                                                                                      |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Product name     | `𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍.`                                                                                                                                                                                                                                          |
| Tagline          | `Release engine for AI-built software.`                                                                                                                                                                                                                 |
| Secondary line   | `Ship AI-built software without the vibe hangover.`                                                                                                                                                                                                     |
| Website          | `https://vibecrafted.io/`                                                                                                                                                                                                                               |
| Repository       | `https://github.com/vetcoders/vibecrafted`                                                                                                                                                                                                              |
| Docs             | `https://vibecrafted.io/en/quickstart/`                                                                                                                                                                                                                 |
| Category         | `Developer Tools`, `AI Agents`, `Release Engineering`                                                                                                                                                                                                   |
| Pricing          | `Free for personal use and startups. Enterprise licensing available.`                                                                                                                                                                                   |
| Primary CTA      | `curl -fsSL https://vibecrafted.io/install.sh \| bash` on macOS, Linux, WSL2                                                                                                                                                                            |
| Backup CTA       | Signed DMG from the [latest release](https://github.com/vetcoders/vibecrafted/releases/latest) when one is attached; otherwise `git clone` + `make install`                                                                                             |
| 160-char summary | `Vibecrafted hardens AI-generated repos through structural mapping, convergence loops, install audits, and launch-ready packaging.`                                                                                                                     |
| 300-char summary | `Vibecrafted is the release engine for AI-built software. It takes the repo your agents already produced and drives it through perception, verification, convergence loops, install truth, and launch-readiness work until the product is fit to ship.` |

## Form-specific fill hints

| Surface                | What the form wants most                        | Best asset stack                                                   | Use this copy block                |
| ---------------------- | ----------------------------------------------- | ------------------------------------------------------------------ | ---------------------------------- |
| There’s An AI For That | Clear category fit + immediate usefulness       | Guided installer screenshot + command deck                         | TAAFT blurb                        |
| Future Tools           | Founder-readable before/after                   | Guided installer screenshot + quickstart screenshot                | Future Tools blurb                 |
| Futurepedia            | Business-readiness and professional credibility | Guided installer screenshot + quickstart + short walkthrough video | Futurepedia blurb                  |
| Toolify                | SEO keywords and conversion clarity             | Guided installer screenshot + portal hero + pricing clarity        | Toolify blurb                      |
| TopAI.tools            | Concise value + discoverability                 | Guided installer screenshot + command deck                         | TopAI.tools blurb                  |
| Uneed                  | Launch-day story and polished visuals           | Guided installer screenshot + portal hero + short walkthrough      | Uneed emphasis                     |
| Product Hunt           | Narrative, maker comment, demo                  | 5 screenshots + short walkthrough/demo + maker comment             | Product Hunt blurb + first comment |

## Paste-ready marketplace blurbs

These are intentionally short so they fit the common "summary" or
"why should we feature this?" text boxes without further editing.

- There's An AI For That
  Vibecrafted is the release engine for AI-built software. It takes the repo your agents already produced and pushes it through structural mapping, convergence loops, install truth, and launch-readiness work until a real stranger can install it and trust it.

- Future Tools
  Cursor, Claude, Codex, and app builders can produce the draft. Vibecrafted is what you run next to turn that draft into something shippable: verified install path, sharper docs, stronger product surface, and fewer hidden launch-day lies.

- Futurepedia
  Vibecrafted helps founders and software teams move from AI-generated repo to launch-ready product. It hardens the codebase, verifies install truth, cleans up the public surface, and prepares the release path instead of stopping at code generation.

- Toolify
  AI agents can generate software fast; Vibecrafted makes that output fit for release. It combines structural analysis, convergence loops, guided install, and packaging work so teams can ship AI-built products without the vibe hangover.

- TopAI.tools
  Vibecrafted is usable now from a real public install path and is built for teams shipping AI-generated repos. It focuses on verification, release engineering, onboarding truth, and the gap between "demo works" and "a customer can actually use this."

- Uneed
  Vibecrafted exists for the vibe hangover: when an AI-built repo looks done until install, trust, or launch reality says otherwise. The product gives teams a guided install path, a sharper command deck, and a release workflow that turns draft-shaped repos into something a stranger can actually use.

- Product Hunt
  We built Vibecrafted for the vibe hangover: the moment an AI-built repo looks done until install, docs, or launch reality says otherwise. Vibecrafted is the release engine that closes that gap and gets the product ready for real users.

## Suggested screenshots and assets

1. Browser-based guided installer
   Shows the effortless front door and trust surface.

2. Command deck
   `vibecrafted help` with the phase taxonomy visible.

3. Quick Start surface
   The path from install to `vibecrafted init claude`.

4. Marbles or framework evidence surface
   Shows the convergence loop as the differentiator.

5. Public landing page / portal hero
   Pull from the portal repo once its current live edits settle.

## Source checklist

- There’s An AI For That: `https://theresanaiforthat.com/launch/`, `https://theresanaiforthat.com/get-featured/`
- Future Tools: `https://futuretools.io/submit-a-tool`, `https://www.futuretools.io/faq`
- Futurepedia: `https://www.futurepedia.io/submit-tool`, `https://www.futurepedia.io/verified`
- Toolify: `https://www.toolify.ai/submit`
- TopAI.tools: `https://topai.tools/submit`
- Uneed: `https://www.uneed.best/submit-a-tool`, `https://www.uneed.best/launch-guide`
- Product Hunt: `https://help.producthunt.com/en/articles/481909-how-can-i-get-access-to-post`, `https://www.producthunt.com/launch/before-launch`
- Adjacent-tool signal checks:
  `https://theresanaiforthat.com/ai/cursor-ai/`
  `https://www.futuretools.io/tools/cursor`
  `https://futuretools.io/tools/devin-ai`
  `https://www.futuretools.io/tools/bolt-new`
  `https://www.futurepedia.io/tool/cognition-ai`
  `https://www.futurepedia.io/tool/build-ai`
  `https://www.toolify.ai/tool/cursor`
  `https://www.toolify.ai/tool/windsurf`
  `https://topai.tools/t/windsurf-ai-ide-codeium`

## First Product Hunt comment draft

We built Vibecrafted because AI can generate a repo faster than most teams can reality-check it. The result is the vibe hangover: code that looks done until auth breaks, the release path lies, or the landing page cannot explain the product. Vibecrafted is the release engine for that gap. It maps the repo, pulls back creator intent, runs convergence loops, audits the product surface, and pushes the whole thing toward something a stranger can install, trust, and actually use.
