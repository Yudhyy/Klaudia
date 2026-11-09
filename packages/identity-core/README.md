# Identity core

Account registration and login use cases with no third-party dependencies.
Callers supply account storage, password hashing and verification, and token
issuance through the interfaces in `identity_core.ports`.

`IdentityService` accepts validated `Registration` and `Login` requests and returns
an `AuthenticatedAccount`. It rejects existing usernames and invalid credentials.
Registration saves a password hash before it issues a token. Login records the
successful login before it issues a token.

The application owns HTTP validation and error mapping, PostgreSQL, bcrypt, JWT
configuration, and resource lifecycle. This package adds no roles, refresh tokens,
account deletion or token revocation.
