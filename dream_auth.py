"""Accept wake-up calls only from this repository's scheduled workflow."""
import jwt

ISSUER = "https://token.actions.githubusercontent.com"
REPOSITORY = "Lshfn/glitch-telegram-bot"
WORKFLOW_REF = REPOSITORY + "/.github/workflows/memory-dreams.yml@refs/heads/main"
KEYS = jwt.PyJWKClient(ISSUER + "/.well-known/jwks", cache_keys=True, timeout=10)


def authorize_dream_tick(authorization, audience):
    if not audience or not authorization.startswith("Bearer ") or len(authorization) > 16384:
        raise jwt.InvalidTokenError("Missing workflow authentication")
    token = authorization[7:]
    if jwt.get_unverified_header(token).get("alg") != "RS256":
        raise jwt.InvalidTokenError("Unexpected signing algorithm")
    key = KEYS.get_signing_key_from_jwt(token).key
    claims = jwt.decode(token, key, algorithms=["RS256"], audience=audience, issuer=ISSUER,
                        options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub",
                                             "repository", "repository_id", "repository_owner_id",
                                             "ref", "workflow_ref", "event_name"]})
    if (claims["repository"] != REPOSITORY or str(claims["repository_id"]) != "1170794745" or
            str(claims["repository_owner_id"]) != "93941329" or claims["ref"] != "refs/heads/main" or
            claims["workflow_ref"] != WORKFLOW_REF or claims["event_name"] not in ("schedule", "workflow_dispatch")):
        raise jwt.InvalidTokenError("Untrusted workflow")
    return True
