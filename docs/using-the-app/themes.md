---
title: Themes
aliases:
  - "interface/themes"
---

Lex App supports two carefully crafted visual themes — **Light** and **Dark** — so you can work comfortably in any environment. Switch between them instantly using the toggle in the application header.

## Light Mode

The default experience. Clean white backgrounds with a professional Slate color palette:

- **Primary**: Navy (#283C50) for the side navigation, headers, and page chrome
- **Accent**: Teal (#14B4B4) for buttons, links, and anything actionable
- **Backgrounds**: A soft app background (#F2F5F8) with white surfaces (#FFFFFF) for cards and tables
- **Accents**: Subtle separators and shadows that give depth without distraction

Light mode is ideal for well-lit offices, presentations, and daytime use. The high contrast between text and background minimizes eye strain during extended data entry sessions.


## Dark Mode

Designed for low-light environments, late-night work, and users who simply prefer a darker interface:

- **Primary**: A lighter teal (#4fd1d1) for interactive elements — the navy used in light mode would disappear against a dark ground
- **Backgrounds**: Navy black (#12202c) with deep navy surfaces (#1a2d3e) that reduce screen glare
- **Text**: Soft white (#F2F5F8) and muted slate (#9fb0bd) for readability without harshness

Dark mode isn't just a color inversion — every component is individually styled. The data grid, sidebar, cards, buttons, and even the Streamlit dashboard frames are all tuned for visual consistency.


## How to Switch

Click the **theme toggle** icon in the application header bar, next to the search field. The switch is instant — no reload required. Your preference is remembered across sessions.


> [!tip]
> Both themes are fully compatible with all features: the [[using-the-app/the-grid/index|data grid]], [[using-the-app/record-detail/index|record detail tabs]], embedded [[using-the-app/record-detail/analytics tab|Streamlit dashboards]], and [[using-the-app/the-grid/exporting data|exported files]] all respect your chosen theme.

### Streamlit dashboards

Embedded Streamlit pages follow the Lex App theme by default, so the dashboard
and the surrounding application stay in sync. Set `LEX_THEME_FOLLOW=0` when a
dashboard needs to keep its own theme controls. While following is enabled,
Streamlit's own theme menu cannot override the mode selected in Lex App.
