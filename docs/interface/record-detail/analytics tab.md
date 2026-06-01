---
title: Analytics Tab
---

Numbers in a grid tell part of the story. A chart tells the rest. The Analytics tab embeds a live [Streamlit](https://docs.streamlit.io/) dashboard directly inside the record detail — scoped to the specific record you're viewing.

## How It Works

When you open the Analytics tab, the application:

1. **Authenticates** with the Streamlit server using your existing session token — no separate login required
2. **Passes the record context** (model name + record ID) so the dashboard knows what to display
3. **Renders the dashboard** in an embedded frame, fully interactive — charts, filters, metrics, all live

From your perspective, it's one click: grid → record → Analytics tab. No context switching, no new browser tabs, no re-authentication.

> [!example]- 🎬 Video — Grid to record to Analytics tab
> <video controls width="100%">
>   <source src="../../videos/record-analytics-tab.mp4" type="video/mp4">
> </video>
> Click from the grid to a record, switch to the Analytics tab, see the Streamlit dashboard load.

## Table-Level vs. Record-Level

Lex App supports two kinds of dashboards, and the Analytics tab handles both:

| Dashboard Type | What It Shows | Where It Appears |
|---|---|---|
| **Table-level** | Overview metrics for the entire model (all records) | [[interface/the-grid/index\|The Grid]] toolbar or sidebar |
| **Record-level** | Deep-dive analytics for a single specific record | **Analytics Tab** on the record detail page |

The distinction is automatic: when the dashboard receives a record ID (`pk`), it shows record-level analytics. Without one, it shows the table-level overview.

## What You Can Build

Because dashboards are powered by Streamlit, the possibilities are wide. Common examples include:

- 📈 **Trend charts** — how a metric evolves over time for this record
- 📊 **Comparison bars** — this record vs. peers or benchmarks
- 🎯 **KPI cards** — key figures at a glance (total invested, IRR, net value)
- 🗺 **Geographic visualizations** — location data on maps
- 📋 **Custom tables** — derived data not stored in the model itself

All of these run live – they compute from the current data, so they're always up to date.

> [!example]- 📸 Screenshot — Analytics tab with Streamlit dashboard
> ![Analytics tab showing a record-level Streamlit dashboard](../images/record-detail/analytics-tab.jpeg)

## When the Analytics Server Is Offline

If the Streamlit server isn't running or is unreachable, the Analytics tab shows a friendly message:

> 📡 **Analytics Service Unavailable**
> We couldn't connect to the analytics server. It might be down or unreachable at the moment.
> `[Retry Connection]`

Click **Retry Connection** to check again. This ensures you're never stuck — the rest of the record detail (Summary, Timeline, History, Audit Log) works independently of the analytics server.

## Seamless Authentication

The connection between Lex App and the Streamlit dashboard uses **federated authentication**. Your Keycloak access token is passed securely to Streamlit — the dashboard inherits your identity and access level.

This means:
- **No re-login** — you're already authenticated
- **Audit trail preserved** — any action in the dashboard is traceable back to your identity
- **Access control respected** — the dashboard sees only the data you're allowed to see

> [!note]
> For developers: the integration is powered by the `StreamlitIframe` component. See [[features/access-and-ui/streamlit dashboards]] for implementation details.
