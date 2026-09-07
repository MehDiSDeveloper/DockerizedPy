"""Mint the one key pair a deploy needs before it can push anything.

    python -m app.scripts.generate_vapid_keys

Prints two lines to paste into `.env` / `.env.docker` (or into Liara's
environment, which is where the deploy actually reads them from). Generating
is all this does -- it writes no file, because the private key belongs in the
environment and never in the repository.

**Run it once per deploy and keep the pair.** A browser binds its
subscription to the public key it was created with, so a new pair silently
invalidates every device already subscribed: each member's switch keeps
looking on, and nothing arrives until they turn it off and on again. Same
class of decision as rotating `secret_key`, and for the same reason -- the
credential is the only thing tying an existing handle to this server.
"""

from app.webpush import generate_vapid_keys


def main() -> None:
    private_key, public_key = generate_vapid_keys()
    print("VAPID_PRIVATE_KEY=" + private_key)
    print("VAPID_PUBLIC_KEY=" + public_key)


if __name__ == "__main__":
    main()
