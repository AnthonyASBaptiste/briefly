# DESIGN.md — Briefly Design System

## Aesthetic & Philosophy
Briefly is designed with an editorial, trustworthy, and calm aesthetic tailored for legal practitioners. It rejects generic SaaS AI tropes (no purple gradients, no floating blobs, no decorative emojis) in favor of deep pine teal, crisp warm paper, and clear typographic hierarchy reminiscent of premium legal stationery.

## Color Palette

### Core Tokens
- **Background (`--paper`)**: `#f5f7f2` — warm, low-glare editorial paper.
- **Primary Ink (`--ink`)**: `#142d27` — deep forest slate, high contrast (13.5:1 ratio).
- **Secondary / Muted (`--muted`)**: `#465d56` — balanced slate-sage, passes WCAG AA contrast (6.5:1 on paper, 7.1:1 on cards).
- **Cards & Surfaces (`--card`)**: `#ffffff` — clean elevated white with subtle 1px border (`--line: #e0e8e1`).
- **Brand Accent (`--green`)**: `#205e49` — steady forest green for primary actions.
- **Success Soft (`--green2`)**: `#e5f2ea` — soft sage badge background.
- **Attention / Review (`--amber`)**: `#8c5c0c` — warm amber for review queue items.
- **Attention Soft (`--amber2`)**: `#faefd4` — soft amber pill background.
- **Danger (`--red`)**: `#a1433c` — subdued terracotta for destructive warnings.

## Typography

### Font Stack
- Primary: `Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif`
- Form elements inherit the primary stack directly to avoid default serif fallback leaks.

### Scale & Hierarchy
- **Brand H1**: `26px` / weight 750 / letter-spacing `-0.6px` / `text-wrap: balance`
- **Section H2 (Hero)**: `21px` / weight 700 / letter-spacing `-0.4px`
- **Modal Title H2**: `20px` / weight 700 / letter-spacing `-0.3px`
- **Panel Titles H3**: `15px` / weight 700 / letter-spacing `-0.2px`
- **Body & Metrics**: `15px` regular / `28px` bold metric numbers
- **Captions & Metadata**: `12px` - `13px` muted

## Spacing & Elevation
- Base radius: `9px` on buttons, `13px` on cards/panels, `17px` on hero banner.
- Shadow: `0 4px 18px rgba(16,42,34,0.06), 0 1px 3px rgba(16,42,34,0.04)`
- Interactive touch targets: minimum `44px` height on all buttons, select menus, and form inputs (WCAG 2.5.5), with compact inline controls (e.g. `.btn-xs`) at `38px`.
- Keyboard navigation: visible 2px outline with 2px offset (`outline: 2px solid var(--green)`).
