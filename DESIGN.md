---
version: alpha
name: Excellence Cloud LEX AI Release Communications
description: Design system for Excellence Cloud product-release emails and lightweight LEX AI marketing surfaces, derived from the LEX AI 0.2.1 HTML release announcement.
colors:
  primary: "#0f172a"
  primary-container: "#1e293b"
  secondary: "#1d4ed8"
  secondary-container: "#dbeafe"
  tertiary: "#38bdf8"
  tertiary-container: "#eff6ff"
  neutral: "#ffffff"
  neutral-soft: "#f8fafc"
  neutral-page: "#eef4fb"
  border: "#e2e8f0"
  border-accent: "#bfdbfe"
  text-primary: "#0f172a"
  text-secondary: "#475569"
  text-muted: "#64748b"
  text-inverse: "#ffffff"
  text-inverse-muted: "#cbd5e1"
typography:
  hero-title:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 36px
    fontWeight: 700
    lineHeight: 44px
    letterSpacing: 0em
  section-title:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 26px
    fontWeight: 700
    lineHeight: 34px
    letterSpacing: 0em
  card-title:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 17px
    fontWeight: 700
    lineHeight: 24px
    letterSpacing: 0em
  body:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 16px
    fontWeight: 400
    lineHeight: 26px
    letterSpacing: 0em
  body-sm:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 15px
    fontWeight: 400
    lineHeight: 24px
    letterSpacing: 0em
  meta:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 13px
    fontWeight: 400
    lineHeight: 20px
    letterSpacing: 0em
  badge:
    fontFamily: Arial, Helvetica, sans-serif
    fontSize: 12px
    fontWeight: 700
    lineHeight: 16px
    letterSpacing: 0.3px
  code:
    fontFamily: Consolas, "SFMono-Regular", Menlo, Monaco, monospace
    fontSize: 14px
    fontWeight: 400
    lineHeight: 22px
    letterSpacing: 0em
rounded:
  sm: 10px
  md: 12px
  lg: 14px
  xl: 16px
  container: 20px
  logo: 22px
  pill: 999px
spacing:
  xs: 6px
  sm: 8px
  md: 12px
  lg: 18px
  xl: 24px
  section: 36px
  hero-y: 46px
  container-gutter: 12px
components:
  page-background:
    backgroundColor: "{colors.neutral-page}"
    textColor: "{colors.text-primary}"
  email-container:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.container}"
    width: 640px
  top-bar:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.secondary-container}"
    typography: "{typography.meta}"
    padding: 14px
  hero:
    backgroundColor: "{colors.tertiary-container}"
    textColor: "{colors.text-primary}"
    padding: 36px
  badge-new-release:
    backgroundColor: "{colors.secondary-container}"
    textColor: "{colors.secondary}"
    typography: "{typography.badge}"
    rounded: "{rounded.pill}"
    padding: 8px
  stats-card:
    backgroundColor: "{colors.neutral-soft}"
    textColor: "{colors.text-primary}"
    rounded: "{rounded.lg}"
    padding: 22px
  content-card:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.text-secondary}"
    rounded: "{rounded.lg}"
    padding: 22px
  code-block:
    backgroundColor: "{colors.neutral-soft}"
    textColor: "{colors.text-primary}"
    typography: "{typography.code}"
    rounded: "{rounded.md}"
    padding: 14px
  button-primary:
    backgroundColor: "{colors.secondary}"
    textColor: "{colors.text-inverse}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: 14px
  button-secondary:
    backgroundColor: "{colors.tertiary-container}"
    textColor: "{colors.secondary}"
    typography: "{typography.body-sm}"
    rounded: "{rounded.sm}"
    padding: 14px
  dark-cta-panel:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.text-inverse}"
    rounded: "{rounded.xl}"
    padding: 28px
  footer:
    backgroundColor: "{colors.neutral-soft}"
    textColor: "{colors.text-muted}"
    typography: "{typography.meta}"
    padding: 24px
---

## Overview

Excellence Cloud’s LEX AI release communications should feel like a production-grade enterprise software announcement: clear, technical, secure, and easy to act on. The visual identity combines a dark navy technical foundation with bright cloud-blue interaction colors and soft blue surfaces. The result should communicate trust, operational maturity, and practical momentum rather than hype.

This design system is optimized for HTML email first, then reusable for small product-release landing pages, upgrade notices, release-note cards, and customer onboarding surfaces. Email implementations should prefer table-safe layout, inline CSS, conservative typography, and high-contrast CTA buttons.

Core brand attributes:

- **Technical clarity:** readers should immediately understand what changed, why it matters, and what action to take.
- **Enterprise confidence:** the layout should feel stable, calm, secure, and deployment-oriented.
- **Low-friction onboarding:** installation commands, upgrade steps, and support actions must be visually prominent.
- **Human support:** every release should include clear paths to ask a question, book a walkthrough, or request upgrade support.

## Colors

The palette is a cool enterprise-cloud system built around navy, blue, sky blue, and slate neutrals.

- **Primary (#0f172a):** Deep navy for hero text, dark CTA panels, and serious product framing.
- **Primary container (#1e293b):** Dark slate used for secondary dark-panel gradients.
- **Secondary (#1d4ed8):** Main action blue for primary buttons, important labels, and link-like emphasis.
- **Secondary container (#dbeafe):** Soft blue pill and badge background; use for release labels and light emphasis.
- **Tertiary (#38bdf8):** Bright cloud-blue accent for energetic CTA moments and gradient endpoints.
- **Tertiary container (#eff6ff):** Pale blue hero and secondary-button surface.
- **Neutral (#ffffff):** Main card and email body surface.
- **Neutral soft (#f8fafc):** Command blocks, release-note boxes, and light nested cards.
- **Neutral page (#eef4fb):** Outer email background; should remain soft and unobtrusive.
- **Border (#e2e8f0):** Default structural border for cards and separators.
- **Border accent (#bfdbfe):** Blue-tinted border for secondary actions and command blocks.
- **Text primary (#0f172a):** Headlines and strong labels.
- **Text secondary (#475569):** Main paragraph copy.
- **Text muted (#64748b):** Metadata, footer content, helper notes.
- **Text inverse (#ffffff):** Text on strong blue or dark backgrounds.
- **Text inverse muted (#cbd5e1):** Body copy inside dark CTA panels.

Use gradients sparingly. The approved gradient direction is diagonal or vertical from deep navy through action blue into cloud blue. Gradients should appear in the top strip, hero accent areas, or final CTA panels, not in every card.

## Typography

Use Arial with system fallbacks for broad email-client compatibility. Do not introduce decorative brand fonts in customer emails unless there is a tested fallback and the layout still works without them.

- **Hero title:** bold, large, compact, and direct. Use for one decisive product message, such as “Setup just got simpler.”
- **Section title:** bold and functional. Use for content blocks like “Upgrade Guide” and “Why This Matters.”
- **Card title:** bold and instructional. Use for numbered steps, feature summaries, or small module headers.
- **Body:** calm, readable paragraph copy. Avoid dense sales language.
- **Body small:** supporting explanations, CTA labels, and compact prose.
- **Meta:** release labels, footer metadata, top-bar labels, and small legal/company details.
- **Badge:** uppercase or short-label treatment for “NEW RELEASE,” version tags, or status chips.
- **Code:** monospaced command text. Preserve readable spacing and allow wrapping on mobile.

Tone should be precise and product-led. Prefer “A new version of LEX AI is available” over exaggerated claims. Explain concrete operational benefits: fewer secrets, fewer credentials, simpler setup, faster onboarding.

## Layout

The email layout uses a centered 640px container on a soft blue page background. Preserve this width for desktop email clients unless a specific campaign requires a narrower transactional style.

Layout rules:

- Use a single-column structure for the main message.
- Use table-based layout for production HTML email.
- Keep the top strip thin and informative: company name on the left, release/version metadata on the right.
- Start with a hero area containing logo, badge, headline, release summary card, short customer greeting, and first CTA row.
- Follow the hero with action-oriented content sections: upgrade guide first, benefits second, final CTA third.
- Put release notes and legal/company footer after the main CTA.
- Use generous horizontal padding: 36px desktop, 20px mobile.
- Mobile breakpoint should stack multi-column rows and center top-bar metadata.

Spacing rules:

- Use 36px horizontal section padding in desktop email.
- Use 20px horizontal padding on mobile.
- Use 18–24px vertical rhythm between major content elements.
- Keep dense technical areas inside bordered cards so command text does not visually overwhelm the reader.

## Elevation & Depth

Depth should be subtle and functional. The brand should not feel like a glossy SaaS template.

- Use soft shadows only around the logo tile or isolated floating brand marks.
- Use borders and surface contrast for most structure.
- Use dark CTA panels to create hierarchy near the end of a message.
- Avoid heavy drop shadows on every card; email clients may render them inconsistently.
- Prefer light borders (#e2e8f0 or #bfdbfe) over complex layered shadows.

## Shapes

The system uses rounded but not playful geometry.

- Main email container: 20px radius.
- Logo tile: 22px radius to feel app-like and premium.
- Content cards: 14px radius.
- Code blocks and secondary cards: 12px radius.
- Buttons: 10px radius.
- Badges and copy pills: 999px radius.

Do not use sharp-corner enterprise styling. Do not over-round large sections into bubbly consumer-app shapes.

## Components

### Page background

Use the soft blue page background around the entire email. It frames the white container and reinforces the cloud identity without competing with content.

### Email container

Use a white 640px centered container with 20px radius and hidden overflow. This creates a clean product-card feel and keeps the message bounded.

### Top bar

Use a compact navy-to-blue gradient strip. It should contain “Excellence Cloud” and release metadata only. Do not add navigation links to the top bar in release emails.

### Hero

The hero should carry the main announcement. It may use a vertical pale-blue-to-white gradient. Include:

1. Logo tile or product mark.
2. Status badge.
3. One strong headline.
4. Compact release summary card with three items.
5. Greeting and concise announcement copy.
6. Primary and secondary CTAs.

### Release summary card

Use three equal columns on desktop and stacked rows on mobile. Suggested labels:

- Release
- Key Information
- Outcome

Each item should pair muted metadata with a bold value.

### Buttons

Primary buttons use secondary blue with white text. Secondary buttons use pale blue with blue text and an accent border. Button labels should be action-specific: “Ask a Question,” “Book a Walkthrough,” “Request Upgrade Support,” or “Schedule a Demo.”

Email CTAs should use real links and must not depend on JavaScript. For email, avoid relying on hover-only states.

### Upgrade guide cards

Use bordered cards with clear numbered steps. Commands should be separated by platform, with monospaced command blocks. If copy buttons are included in a web version, provide a non-JavaScript fallback because most email clients will strip scripts.

### Code blocks

Use neutral-soft backgrounds, blue-accent borders, 12px radius, and monospaced text. Allow wrapping for long commands. Never place critical command text only inside images.

### Benefits card

Use a simple text list for “Why This Matters.” Bullets may be rendered as text symbols in email for compatibility. Keep each bullet benefit-oriented and operational.

### Dark CTA panel

Use a dark navy gradient near the end of the email to restate the production value and provide final actions. The panel should feel confident, not alarmist.

### Release note

Use a muted light card after the main CTA for caveats, compatibility notes, or deployment-specific reminders.

### Footer

Footer content should be centered, small, and muted. Include company name, address, and required legal details. Keep it visually separate with a top border.

## Do's and Don'ts

### Do

- Use exact token colors rather than approximate blues.
- Keep the message technical, concrete, and customer-helpful.
- Put the most important customer action above the fold.
- Preserve high contrast for buttons and dark panels.
- Use table-safe, inline-style-friendly patterns for email.
- Keep installation and upgrade commands selectable as text.
- Stack columns on mobile and center metadata where needed.
- Include support and scheduling paths in release communications.
- Treat the release version as metadata and the customer outcome as the headline.

### Don't

- Do not make the release email feel like a generic newsletter.
- Do not introduce new colors outside the token palette.
- Do not use JavaScript-dependent behavior for critical email interactions.
- Do not hide upgrade steps behind a single “Learn More” link.
- Do not overuse gradients or shadows.
- Do not use vague hype such as “revolutionary” when the benefit is operational simplicity.
- Do not place legal/company footer details in low-contrast colors.
- Do not rely on remote images for essential content; the message should still work if images are blocked.
