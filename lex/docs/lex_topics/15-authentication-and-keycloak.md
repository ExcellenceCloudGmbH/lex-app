# Authentication & Keycloak

Search keywords: keycloak, UMA, RBAC, scopes, user permissions

## Scope

- Auth model and role/scope system
- Environment-driven configuration and profile mapping

## Key Points

- Lex uses Keycloak-integrated authentication and scope-based authorization.
- Permission model combines model/resource scopes with user/client role context.
- Environment variables define issuer/client/realm integration settings.
- Auth context feeds into `UserContext` and serializer permission filtering.

## Where to Expand

- `lex_context.md`: Framework Overview (auth mentions)
- `lex_context_repo.md`: Authentication & Keycloak

## LLM Prompt Starters

- "Given these scopes/roles, determine allowed operations for this model and explain briefly."
- "List required Keycloak env vars for this deployment scenario and common misconfigurations."
