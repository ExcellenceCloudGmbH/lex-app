# Publishing release notes to the help centre

When a release is published on GitHub, its note is also published to quackback as a help-centre
article. Nothing to run by hand.

## What gets published

The **approved** body — the one on the GitHub release after a human has reviewed and possibly
edited it. Not the drafted one. The help centre should carry what somebody signed off.

| Article field | Comes from |
|---|---|
| `title` | `LEX v2.1.9` |
| `slug` | `lex-release-v2-1-9` |
| `content` | the release body, verbatim |
| `description` | the first real sentence of the note |
| `categoryId` | the `QUACKBACK_CATEGORY_ID` variable |

## Republishing does not duplicate

Publishing is idempotent by slug. If a release note is edited and the job runs again, the existing
article is **updated**, not added a second time.

The slug comes from the tag, not the title — a human may edit the title, and an edited title must
not silently create a second article for one release. And because quackback's `search` is fuzzy,
the slug is compared exactly against each result before anything is overwritten: a near match like
`v2.1.90` must never overwrite `v2.1.9`.

## It cannot fail a release

Every failure path returns a reason instead of raising, and the step is `continue-on-error` on top
of that. By the time it runs, the note is already public on GitHub — a help centre one article
behind is a nuisance; a red release is an incident.

Missing configuration is reported as `skipped: QUACKBACK_API_KEY is not set` rather than treated as
an error, so the pipeline is safe to merge before the settings exist.

## Configuration

| Name | Kind | Example |
|---|---|---|
| `QUACKBACK_API_KEY` | **secret** | `qb_…` |
| `QUACKBACK_BASE_URL` | variable | `https://support.example.com/api/v1` |
| `QUACKBACK_CATEGORY_ID` | variable | `cat_…` |

The base URL ends in `/api/v1` — quackback's OpenAPI spec declares that as its server prefix, so
the paths this code builds are relative to it.

To find the category id:

```bash
curl -sS -H "Authorization: Bearer $QUACKBACK_API_KEY" \
  "$QUACKBACK_BASE_URL/help-center/categories" | jq '.data[] | {id, name}'
```

### About the key

The key is a bearer token with write access to the help centre. It belongs in the repository's
**secrets**, never in a file, a command line, or a chat message — a command line reaches process
listings, and chat logs get backed up and searched.

`publish` redacts the key from anything it returns, because errors echo their input: a 401 body or
a proxy error can carry the `Authorization` header back, and the return value goes into a workflow
log. GitHub Actions masks secrets it injected, but the same string can reach a job summary or an
artifact, and a key is not worth trusting one layer of masking with.

## The API this uses

From quackback's own `apps/web/openapi.json`:

```
GET   /help-center/articles          query: categoryId, status, search, cursor, limit
POST  /help-center/articles          required: categoryId, title, content
                                     optional: slug, description, authorId
PATCH /help-center/articles/{id}     any of the create fields
auth  Authorization: Bearer qb_...
```

If quackback changes that schema, `release_notes/quackback.py` is the only place to update, and its
tests pin the request shape.
