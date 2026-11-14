---
title: "Custom domains"
---

A hosted instance is reachable at `<subdomain>.lit.<platform domain>` from the moment it is created. A **custom domain** is a hostname you own — `app.customer.com` — pointed at that same instance, so your users never see the platform's address.

The platform hostname never goes away. It keeps resolving through the platform's own DNS and certificate, which is what operator access and uptime probing use, precisely so that neither depends on your DNS.

| Page section                                                                              | Answers                                                                |
| ----------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| [What gets attached](#what-gets-attached)                                                 | The application, and a hostname per dashboard                          |
| [What happens to the platform address](#what-happens-to-the-platform-address)             | Attached, promoted, and the way back                                   |
| [Why the dashboards get their own hostnames](#why-the-dashboards-get-their-own-hostnames) | The browser rule that makes a shared domain necessary, not merely tidy |
| [What the instance has to know](#what-the-instance-has-to-know)                           | The variables, and what reads each one                                 |

## What gets attached

A custom domain is attached in the Instance Controller, under an instance's **Domains** tab. You give the application hostname — `app.customer.com`, or the root `customer.com` — and tick which of the instance's dashboards should move with it. Each dashboard becomes a label in front of the application hostname:

| Role                 | Hostname                     |
| -------------------- | ---------------------------- |
| The application      | `app.customer.com`           |
| Streamlit dashboards | `streamlit.app.customer.com` |
| Reflex dashboards    | `reflex.app.customer.com`    |

Each one is a separate hostname at the edge, with its own DNS record for you to create, its own ownership check and its own certificate, so they can be in different states at the same time. The Domains tab shows the records and the state of each.

A dashboard switched on later can be added to a domain that is already attached — the wizard offers the dashboard rather than asking for the hostname again. Switching a dashboard **off** gives its hostname back automatically: a hostname routed to a dashboard that no longer runs would answer `503`.

The root domain is attachable, with one caveat that is not ours: a `CNAME` is illegal at a zone apex, so an apex needs your DNS provider's ALIAS, ANAME or CNAME-flattening support. Cloudflare, Route 53, DNSimple and NS1 all have it; many registrar-bundled DNS services do not, in which case use a subdomain. The wizard says which case you are in before you create any records.

## What happens to the platform address

Attaching and promoting are deliberately two steps.

**Attached.** The hostname is registered at the edge and added to the instance's ingress and to its sign-in client straight away, before your DNS has propagated. By the time the name resolves, the instance is already answering on it — there is no window where the domain works and returns `404`. The platform hostname is still the instance's address: generated links, the sign-in redirect and the API all still name it.

**Promoted.** The custom hostname becomes the address the instance presents itself as. It is what generated links use, what the running application sees as its own host, and where sign-in comes back to. The platform hostname stops serving and permanently redirects there, apart from `/api/health`, which keeps answering directly so that platform probing does not depend on your DNS.

Promotion happens on its own once the certificate is live, unless you clear _"Make this the instance's address as soon as it is verified"_ when attaching. It is retried on each check until it succeeds, so a promotion that could not run at that moment is not lost.

**Switching back** is one click. The role returns to its platform hostname, which starts serving again, and the custom hostname redirects there instead — the way out when a DNS change breaks a domain. A hostname you switch back by hand stays switched back; it is not re-promoted by the next check.

> [!warning] Anyone signed in has to sign in again
> Sessions belong to the hostname that issued them, so promoting — or switching back — ends the sessions on the old address. Both directions say so before they act.

## Why the dashboards get their own hostnames

A Streamlit or Reflex dashboard is shown inside an `<iframe>` in the application. When the frame's origin is on a different _registrable domain_ from the page around it — `customer.com` versus `excellence-cloud.de` — the browser treats it as a third-party context. Safari blocks such a frame's cookies outright and Chrome is removing them, so the dashboard inside cannot hold the session it just signed in with: it loads, is refused, and reloads.

That is why the dashboards are offered alongside the application domain rather than left on the platform host. With `streamlit.app.customer.com` attached, a user on `app.customer.com` is framed a dashboard on their own registrable domain, and the frame is first-party again. The application picks the dashboard hostname per request, from the host the page itself was served on, so the platform hostname and the custom one both work while a domain is only attached.

If no dashboard hostname shares a domain with the page, the canonical one is framed anyway — a third-party frame still works in most browsers, and is better than no dashboard at all.

## What the instance has to know

The hosting platform writes three variables into the instance when, and only when, a custom domain is attached. Each lists every hostname that role answers on, comma separated, canonical first:

| Variable              | Read by                                                                                                                                                                                             |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LEX_APP_HOSTS`       | `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`, so Django accepts the hostname instead of answering `400 Bad Request`; and the dashboard proxy, so a shell on any of them may renew a dashboard's token |
| `LEX_STREAMLIT_HOSTS` | The dashboard proxy, which builds its sign-in callback from the hostname the request arrived on — an allow-list, so a forged `Host` header cannot steer the redirect                                |
| `LEX_REFLEX_HOSTS`    | Reflex's websocket CORS, and the frame's choice of dashboard hostname                                                                                                                               |

Without them an instance only knows its canonical hostname, which is exactly the behaviour of an instance that has no custom domain. That is also why an instance has to run a recent enough infrastructure module before a domain can be attached to it: an older one routes the hostname without telling the processes behind it, and they reject what they have never heard of. The Instance Controller refuses the attach and says which version is needed.

Nothing here is set by hand on a hosted instance. They are documented because they are what a symptom points at: a domain that resolves and returns `400` is a hostname missing from `LEX_APP_HOSTS`; a dashboard that signs in forever is a sign-in callback built for a different hostname.

Two more settings read hostnames, and each needs the custom ones as well:

- **`LEX_INSTANCE_HOSTS`, when dashboards sign in with `LEX_STREAMLIT_AUTH=handoff` or `dual`.** That sign-in does not read `LEX_STREAMLIT_HOSTS`, which serves only the `legacy` one. It hands the dashboard's frame to the application and back, and accepts only the hosts this JSON lists — the `canonical`, `platform` and `extra` hosts of its `app` and `streamlit` entries — or, without it, `DOMAIN_HOSTED` and `STREAMLIT_URL`. The custom hostnames of both the application and the dashboard belong in the `extra` lists: the hand-off goes to the application host on the dashboard's own domain. The platform sets this variable as well. A dashboard that says *this host is not one of the instance's dashboard hosts* is a hostname missing from it. See [[reference/Environment Variables#Sign-in sessions|Environment Variables]].
- **`LEX_EMBED_ALLOWED_ORIGINS`, once it is set.** It then decides which sites may show the application's pages in a frame, and nothing is added to it for you, the dashboard hostnames included. A dashboard on `https://streamlit.app.customer.com` that shows tables with `lex_view`, or the app controls, has to be listed there beside its platform address; a frame the browser refuses to show is an origin missing from it. See [[access-and-dashboards/streamlit/embedding app pages#Allowed sites|Allowed sites]].

## Related

- [[reference/Environment Variables|Environment variables]] — the three host lists, and the dashboards' own settings
- [[access-and-dashboards/streamlit/sessions and authentication|Streamlit: Sessions & Authentication]] — what the dashboard proxy does with a session
- [[access-and-dashboards/reflex/running and deploying|Reflex: Running & Deploying]] — why `REFLEX_API_URL` stays unset when there is more than one hostname
