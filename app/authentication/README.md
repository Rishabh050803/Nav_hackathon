# Authentication

The account API uses the existing `app_user` table. Registration stores an individually salted scrypt password hash in `app_user.password`. The module requires no database migration. Existing accounts without a compatible local password can still use a trusted developer token for local testing.

Set `AUTH_TOKEN_SECRET` to a private, random value of at least 32 characters before starting the server. The same secret verifies tokens on every server instance.

| Method | Endpoint | Request and result |
| --- | --- | --- |
| POST | `/api/auth/register` | Send `username`, `password`, `first_name`, and optionally `last_name`. Returns the public profile with `201`; an existing username returns `409`. |
| POST | `/api/auth/login` | Send `username` and `password`. Returns a bearer token valid for one hour. Invalid credentials return `401`. |
| GET | `/api/auth/me` | Send `Authorization: Bearer <token>` to retrieve the signed-in profile. |

Usernames are case-sensitive. New passwords must be 8 to 256 characters. Send passwords only in JSON request bodies over HTTPS in deployed environments.

Use the login token in the `Authorization` header for exam generation, conduction, evaluation, and analysis. Generation also accepts the existing API-key headers for trusted server callers. When the token expires, log in again.
