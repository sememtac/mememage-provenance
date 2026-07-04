"""Mememage CLI."""
import argparse
import sys


def cmd_verify(args):
    """Verify an image: extract bar, fetch metadata, check hash, extract watermark."""
    from mememage.bar import extract_bar
    from mememage.watermark import extract_watermark
    from mememage.core import fetch_metadata, verify_metadata

    path = args.image
    print(f"Verifying: {path}")

    # Bar
    bar_result = extract_bar(path)
    if bar_result:
        identifier, content_hash = bar_result
        print(f"  Bar: {identifier}")
        print(f"  Hash (bar): {content_hash}")
        print(f"  Soul: {identifier}.soul")
    else:
        print("  Bar: not found")
        identifier = None
        content_hash = None

    # Watermark (per-image extraction if bar provided the content hash)
    wm_hash = extract_watermark(path, content_hash)
    if wm_hash:
        print(f"  Hash (watermark): {wm_hash}")
        if content_hash:
            match = (wm_hash == content_hash)  # watermark now carries the full 16-hex hash
            print(f"  Bar/watermark match: {'YES' if match else 'NO'}")
    else:
        print("  Watermark: not found")

    # Fetch and verify
    if bar_result:
        print(f"\nFetching metadata for {identifier}...")
        try:
            record = fetch_metadata(identifier)
            if record:
                verified = verify_metadata(record)
                if verified is True:
                    print("  WITNESSED — hash matches")
                elif verified is False:
                    print("  ALTERED — hash mismatch!")
                else:
                    print("  UNVERIFIABLE — no content_hash in record")

                # Signature verification
                if record.get("signature") and record.get("public_key"):
                    from mememage.signing import verify as verify_sig
                    sig_ok = verify_sig(
                        identifier, record.get("content_hash", ""),
                        record["signature"], record["public_key"],
                    )
                    if sig_ok is True:
                        fp = record.get("key_fingerprint", "unknown")
                        print(f"  AUTHENTICATED — signature valid (key {fp})")
                    elif sig_ok is False:
                        print("  FORGED — signature invalid!")
                    else:
                        print("  Signature present but cryptography library not installed")

                # Show key fields
                for key in ("prompt", "seed", "sun", "moon", "moon_phase", "rarity_score"):
                    if key in record:
                        val = record[key]
                        if key == "prompt":
                            val = val[:80] + "..." if len(str(val)) > 80 else val
                        print(f"  {key}: {val}")
            else:
                print("  Could not fetch metadata")
        except Exception as e:
            print(f"  Fetch failed: {e}")


def _resolve_password(env_name, prompt):
    """A password for seal/decode WITHOUT putting it in argv (visible in `ps` /
    shell history). From ``--password-env VAR`` if given, else an interactive
    getpass prompt on a TTY, else an error (non-interactive needs the env var)."""
    import getpass
    import os
    if env_name:
        val = os.environ.get(env_name)
        if not val:
            print(f"Error: env var {env_name} is unset/empty", file=sys.stderr)
            sys.exit(1)
        return val
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    print("Error: no password — pass --password-env VAR (non-interactive)",
          file=sys.stderr)
    sys.exit(1)


def cmd_seal(args):
    """Stamp a bar + build an open-hash soul from arbitrary fields."""
    import json as _json
    import os
    import mememage

    fields = {}
    if args.fields:
        try:
            src = sys.stdin.read() if args.fields == "-" else \
                open(args.fields, encoding="utf-8").read()
            loaded = _json.loads(src)
        except Exception as e:
            print(f"Error reading --fields: {e}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(loaded, dict):
            print("Error: --fields must be a JSON object", file=sys.stderr)
            sys.exit(1)
        fields.update(loaded)
    for kv in (args.field or []):
        if "=" not in kv:
            print(f"Error: --field expects KEY=VALUE, got {kv!r}", file=sys.stderr)
            sys.exit(1)
        k, v = kv.split("=", 1)
        fields[k] = v

    # Field visibility — encrypt private fields behind a password.
    password = None
    private = None
    if args.encrypt or args.private:
        password = _resolve_password(args.password_env, "Encrypt password: ")
        if args.private:
            private = [k.strip() for k in args.private.split(",") if k.strip()]

    try:
        result = mememage.encode(args.image, fields, prefix=args.prefix,
                                 identifier=args.identifier,
                                 password=password, private=private)
    except (ValueError, RuntimeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Default record filename is the IDENTIFIER, not the image name — the bar
    # carries the identifier, so binding the filename to it makes the record
    # locatable on any surface by a plain filename/directory find, with no
    # surface-side index. The image name is irrelevant by design; -o overrides.
    # Extension is plain .json for the core; .soul is reserved for the
    # provenance chain's souls.
    out = args.out or os.path.join(
        os.path.dirname(args.image), result.identifier + ".json")
    result.save(out)
    print(f"Encoded {args.image}")
    print(f"  image:        {result.image_path}")
    print(f"  identifier:   {result.identifier}")
    print(f"  content hash: {result.content_hash}")
    if result.record.get("encrypted_fields"):
        print(f"  encrypted:    {len(private) if private else 'all'} field(s)")
    print(f"  record:       {out}")


def cmd_decode(args):
    """Read the bar (identifier + content hash). With --record, also verify it."""
    import json as _json
    import mememage

    bar = mememage.decode(args.image)
    if bar is None:
        print("No Mememage bar in the image.", file=sys.stderr)
        sys.exit(1)

    # Pure read.
    if not args.record:
        if args.json:
            print(_json.dumps({"identifier": bar.identifier,
                               "content_hash": bar.content_hash}))
        else:
            print(f"Bar:  {bar.identifier}")
            print(f"Hash: {bar.content_hash}")
        return

    # --record: verify against a LOCAL record file (no network — resolving is yours).
    try:
        with open(args.record, encoding="utf-8") as f:
            record = _json.load(f)
    except Exception as e:
        print(f"Error reading --record: {e}", file=sys.stderr)
        sys.exit(2)
    v = mememage.verify(args.image, record)

    if args.json:
        print(_json.dumps({"identifier": bar.identifier, "content_hash": bar.content_hash,
                           "match": bool(v), "reason": v.reason}))
        sys.exit(0 if v else 1)

    print(f"Bar:  {bar.identifier}")
    print(f"Hash: {bar.content_hash}")
    if v:
        print("VERIFIED — record matches the image")
        if record.get("encrypted_fields"):
            if args.unlock or args.password_env:
                password = _resolve_password(args.password_env, "Unlock password: ")
                try:
                    view = mememage.unlock(record, password)
                    print("UNLOCKED — private fields decrypted:")
                    _core = ("identifier", "content_hash", "hash_version",
                             "signature", "encrypted_fields")
                    for k, val in sorted(view.items()):
                        if not (k.startswith("_") or k in _core):
                            print(f"  {k}: {val}")
                except Exception:
                    print("(wrong password — could not decrypt)")
            else:
                print("ENCRYPTED — private fields (pass --unlock / --password-env to reveal)")
    else:
        print(f"ALTERED — {v.reason}")
    sys.exit(0 if v else 1)


def cmd_keygen(args):
    """Generate an Ed25519 signing key pair."""
    from mememage.signing import keygen, is_signing_available, get_fingerprint, PRIVATE_KEY_PATH

    if not is_signing_available():
        print("Signing requires the cryptography library.")
        print("Install with: pip install mememage[sign]")
        sys.exit(1)

    if PRIVATE_KEY_PATH.exists() and not args.force:
        fingerprint = get_fingerprint()
        print(f"Key already exists (fingerprint: {fingerprint})")
        print(f"Location: {PRIVATE_KEY_PATH}")
        print("Use --force to overwrite (WARNING: old signed records won't verify with new key)")
        sys.exit(1)

    fingerprint, public_hex, private_path = keygen(force=args.force, name=args.name)
    print(f"Key pair generated.")
    print(f"  Fingerprint:  {fingerprint}")
    print(f"  Public key:   {public_hex}")
    print(f"  Private key:  {private_path}")
    print()
    print("The private key signs your conceptions. Keep it safe, never share it.")
    print("The public key verifies your signature. Publish it everywhere.")


def cmd_rotate(args):
    """Rotate to a new signing key."""
    from mememage.signing import (
        rotate, upload_keychain_record, is_signing_available, get_fingerprint,
    )

    if not is_signing_available():
        print("Signing requires the cryptography library.")
        print("Install with: pip install mememage[sign]")
        sys.exit(1)

    old_fp = get_fingerprint()
    print(f"Rotating key (current: {old_fp})")

    new_fp, succession, chain_id = rotate(name=args.name)

    print(f"New key generated: {new_fp}")
    print(f"Old key archived to ~/.mememage/keychain/")

    if not args.local_only:
        print(f"Uploading succession record to {chain_id}...")
        upload_keychain_record(succession, chain_id, "succession.json")
        print("Succession published. Decoders will follow the chain.")
    else:
        print("Succession record NOT uploaded (--local-only). Upload manually to complete rotation.")
        import json as _json
        print(_json.dumps(succession, indent=2))


def cmd_revoke(args):
    """Publish the pre-signed revocation certificate."""
    from mememage.signing import (
        get_revocation, get_fingerprint, keychain_identifier,
        upload_keychain_record, verify_keychain_record, is_signing_available,
    )

    if not is_signing_available():
        print("Signing requires the cryptography library.")
        print("Install with: pip install mememage[sign]")
        sys.exit(1)

    cert = get_revocation()
    if not cert:
        print("No revocation certificate found at ~/.mememage/revocation.cert")
        print("Generate one with: mememage keygen --force")
        sys.exit(1)

    # Verify the cert is valid before publishing
    ok = verify_keychain_record(cert)
    if ok is not True:
        print("Revocation certificate is invalid or corrupted!")
        sys.exit(1)

    fp = cert.get("key_fingerprint", get_fingerprint())
    chain_id = keychain_identifier(fp)

    print(f"Publishing revocation for key {fp}")
    print(f"WARNING: This permanently marks this key as compromised.")
    print(f"All records signed with this key will show a revocation warning.")

    if not args.yes:
        confirm = input("Type 'REVOKE' to confirm: ")
        if confirm != "REVOKE":
            print("Aborted.")
            sys.exit(0)

    upload_keychain_record(cert, chain_id, "revocation.json")
    print(f"Revocation published to {chain_id}. Key is now dead.")


def cmd_serve(args):
    """Start the mint server in the foreground. Friendly defaults + banner.

    Auto-detects TLS certificates from ~/.mememage/server.json or
    ~/.mememage/certs/. Falls back to plain HTTP if no certs are found
    so new users aren't blocked by certificate setup.
    """
    import logging
    import os
    import sys
    import time
    from pathlib import Path
    from mememage.server import run_server, _get_server_config, _find_free_port

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

    # Desktop/local mode: loopback bind over plain HTTP (localhost is a
    # secure context, so GPS capture still works) and open the dashboard.
    local = getattr(args, "local", False)
    if local:
        args.host = "127.0.0.1"
        args.no_tls = True

    certfile = args.cert
    keyfile = args.key

    if not args.no_tls and not certfile:
        config = _get_server_config()
        cert = config.get("cert")
        key = config.get("key")
        if cert and key and Path(cert).exists() and Path(key).exists():
            certfile, keyfile = cert, key
        else:
            cert_dir = Path("~/.mememage/certs").expanduser()
            if cert_dir.is_dir():
                for c in sorted(cert_dir.glob("*.crt")):
                    k = c.with_suffix(".key")
                    if k.exists():
                        certfile, keyfile = str(c), str(k)
                        break

    if args.no_tls:
        certfile = keyfile = None

    # Public-domain guardrail: if the configured domain looks public
    # (has a dot, isn't localhost/127.*/*.local) AND no MINT_API_TOKEN
    # is set, the dashboard + mint API are open to anyone who can reach
    # the host. That's a real footgun for users deploying on a VPS.
    # Warn loudly + delay 5s so accidental misconfig is obvious; the
    # --force-open flag skips the delay for intentional setups.
    config = _get_server_config()
    domain = (config.get("domain") or "").strip().lower()
    public_looking = (
        domain
        and "." in domain
        and domain != "localhost"
        and not domain.startswith("127.")
        and not domain.endswith(".local")
    )
    # Token presence: env var first (live), then .env file (for users
    # who haven't sourced the file but expect it to take effect).
    token = os.environ.get("MINT_API_TOKEN")
    if not token:
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("MINT_API_TOKEN="):
                    token = line.split("=", 1)[1].strip()
                    break
    if public_looking and not token and not getattr(args, "force_open", False) and not local:
        print()
        print("=" * 72)
        print("  PUBLIC DOMAIN + NO AUTH TOKEN")
        print("=" * 72)
        print(f"  Domain         : {domain}")
        print(f"  MINT_API_TOKEN : (not set)")
        print()
        print("  The mint API and dashboard will be reachable by ANYONE who can")
        print("  resolve this domain. On localhost this is fine; on a VPS or any")
        print("  publicly-routable host it means strangers can trigger mints,")
        print("  read sessions, and operate the dashboard against your identity.")
        print()
        print("  To gate access, set a bearer token in .env:")
        print("      MINT_API_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')")
        print()
        print("  If you intend the server to be open (dev, local network, etc.),")
        print("  pass --force-open to skip this warning.")
        print()
        print("  Starting in 5 seconds...")
        print("=" * 72)
        try:
            time.sleep(5)
        except KeyboardInterrupt:
            print("Aborted.")
            sys.exit(1)

    port = args.port
    if port is None:
        port = _find_free_port("127.0.0.1", 8765) if local else 8443
    run_server(host=args.host, port=port, certfile=certfile, keyfile=keyfile,
               open_browser=local)


def cmd_app(args):
    """Desktop launcher — the friendly one-shot for a local install.

    Runs the mint server on loopback HTTP and opens the dashboard in the
    browser. No domain, no TLS, no token: the OS user boundary is the
    security boundary for a single-user desktop app. This is the entry
    point a packaged double-click binary invokes.
    """
    args.local = True
    cmd_serve(args)


def cmd_dashboard(args):
    """Open the dashboard URL in the browser, or print how to start the server."""
    import webbrowser
    import urllib.request
    import urllib.error
    import ssl
    from mememage.server import _get_server_config

    config = _get_server_config()
    port = args.port or 8443
    domain = args.host or config.get("domain") or "localhost"
    scheme = "https"
    if args.no_tls or not (config.get("cert") and config.get("key")):
        # If we know there are no certs configured, prefer http. The user
        # can still override with --no-tls=False (default) if their cert
        # config lives elsewhere.
        if args.no_tls:
            scheme = "http"

    base = f"{scheme}://{domain}:{port}"
    health_url = f"{base}/health"
    dashboard_url = f"{base}/dashboard"

    # Probe — server may be on http even if we guessed https. Try both.
    reachable = False
    tried = []
    for url in (health_url, health_url.replace("https://", "http://")):
        tried.append(url)
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(url, timeout=2, context=ctx) as r:
                if r.status == 200:
                    reachable = True
                    if url.startswith("http://"):
                        base = base.replace("https://", "http://")
                        dashboard_url = f"{base}/dashboard"
                    break
        except Exception:
            continue

    if not reachable:
        print(f"\u2717 Could not reach the mint server.")
        print(f"  Tried: {', '.join(tried)}")
        print()
        print("  Start it with:")
        print("    mememage serve")
        print()
        print("  Or, if a LaunchAgent is configured, restart it:")
        print(f"    launchctl kickstart -k gui/$(id -u)/com.mememage.mint")
        sys.exit(1)

    print(f"\u2713 Server reachable. Opening {dashboard_url}\u2026")
    webbrowser.open(dashboard_url)


def cmd_chain(args):
    """Manage chains — list, switch, create, migrate."""
    from mememage import chains

    sub = getattr(args, "chain_sub", None)
    if sub == "current":
        print(chains.current())
    elif sub == "list":
        for c in chains.list_chains():
            active = " *" if c.get("id") == chains.current() else "  "
            vis = c.get("visibility", "")
            print(f"{active} {c.get('id', '?'):<20} {c.get('name', ''):<20} {vis}")
        if not chains.list_chains():
            print("(no chains found — run `mememage chain migrate` if you have legacy state)")
    elif sub == "status":
        cid = args.chain_id or chains.current()
        info = chains.info(cid)
        if not info or len(info) == 1:
            print(f"Chain {cid!r} not found.")
            sys.exit(1)
        for k, v in info.items():
            print(f"  {k}: {v}")
    elif sub == "switch":
        try:
            chains.switch(args.chain_id)
            print(f"Active chain: {args.chain_id}")
        except FileNotFoundError as e:
            print(f"\u2717 {e}")
            sys.exit(1)
    elif sub == "new":
        try:
            meta = chains.create(args.chain_id, visibility=args.visibility, name=args.name)
            print(f"Created chain {meta['id']!r} at {chains.CHAINS_ROOT / args.chain_id}")
            for k, v in meta.items():
                print(f"  {k}: {v}")
        except (FileExistsError, ValueError) as e:
            print(f"\u2717 {e}")
            sys.exit(1)
    elif sub == "remove":
        try:
            freed = chains.remove(args.chain_id)
        except FileNotFoundError as e:
            print(f"\u2717 {e}")
            sys.exit(1)
        except RuntimeError as e:
            print(f"\u2717 {e}")
            sys.exit(1)
        print(f"Deleted chain {args.chain_id!r} \u2014 freed {freed / (1024*1024):.1f} MiB")
    elif sub == "reset":
        try:
            result = chains.reset_state(args.chain_id, clear_records=args.clear_records)
        except FileNotFoundError as e:
            print(f"\u2717 {e}")
            sys.exit(1)
        print(f"Reset chunk_state for {args.chain_id!r}. Next mint will be the heart star (\u03b1) of a new constellation.")
        if result.get("backed_up"):
            print(f"  Prior state backed up to: {result['backed_up']}")
        if result.get("records_cleared"):
            print(f"  Local records/ directory cleared (IA records still exist).")
    elif sub == "migrate":
        if not chains.needs_migration():
            print("Nothing to migrate. Either already migrated or no legacy state present.")
            return
        try:
            result = chains.migrate(
                chain_id=args.chain_id or chains.DEFAULT_CHAIN_ID,
                chain_name=args.name or chains.DEFAULT_CHAIN_NAME,
                visibility=args.visibility,
            )
        except FileExistsError as e:
            print(f"\u2717 {e}")
            sys.exit(1)
        print(f"Migrated to {result['target']}:")
        for f in result["moved_files"]:
            print(f"  - {f}")
        for d in result["moved_dirs"]:
            print(f"  - {d}/")
        print()
        print("Restart the mint server to pick up the new paths:")
        print("  launchctl kickstart -k gui/$(id -u)/com.mememage.mint")
        print("  # or: mememage install")
    else:
        print("Usage: mememage chain {current|list|status|switch|new|migrate}")
        sys.exit(1)


def cmd_profile(args):
    """Manage signing-key profiles — list, switch, create, import, alias, remove.

    A profile = one Ed25519 keypair + creator name + revocation cert,
    living under ``~/.mememage/profiles/<id>/``. One profile is "active"
    at any time and signs the next mint. Different profiles can live on
    different machines (laptop vs VPS) so the threat model of remote
    hosting doesn't drag your primary key along for the ride.

    Profiles link into a single human identity through SIGNED RECORDS
    on IA (succession or alias) — never through shared fingerprints or
    seeds. See ``docs/plans/multi-key-profiles.md`` for the why.
    """
    from mememage import profiles, signing

    sub = getattr(args, "profile_sub", None)
    if sub == "list":
        rows = profiles.list_profiles()
        if not rows:
            print("(no profiles found — run any mememage command to auto-migrate, or `mememage profile new <id>`)")
            return
        for r in rows:
            active = " *" if r.get("is_active") else "  "
            fp = r.get("fingerprint") or "(no key yet)"
            name = r.get("name") or ""
            print(f"{active} {r['id']:<18} {fp:<22} {name}")
    elif sub == "active":
        print(profiles.active_id())
    elif sub == "switch":
        try:
            profiles.set_active(args.profile_id)
            print(f"Active profile: {args.profile_id}")
        except FileNotFoundError as e:
            print(f"\u2717 {e}")
            sys.exit(1)
    elif sub == "new":
        try:
            info = profiles.create(args.profile_id, name=args.name)
        except (FileExistsError, ValueError, RuntimeError) as e:
            print(f"\u2717 {e}")
            sys.exit(1)
        print(f"Created profile {info['id']!r} (active)")
        print(f"  fingerprint: {info.get('fingerprint')}")
        if info.get("name"):
            print(f"  creator:     {info['name']}")
        print(f"  public key:  ~/.mememage/profiles/{info['id']}/public.key")
        print(f"  revocation:  ~/.mememage/profiles/{info['id']}/revocation.cert")
    elif sub == "import":
        # Read the key file. Accept ``-`` to mean stdin so the user can
        # pipe `cat id_ed25519 | mememage profile import vps-prod -`.
        from pathlib import Path
        if args.key_file == "-":
            pem_bytes = sys.stdin.buffer.read()
        else:
            pem_bytes = Path(args.key_file).expanduser().read_bytes()
        try:
            info = profiles.import_key(args.profile_id, args.name, pem_bytes)
        except (FileExistsError, ValueError, RuntimeError) as e:
            print(f"\u2717 {e}")
            sys.exit(1)
        print(f"Imported key as profile {info['id']!r}")
        print(f"  fingerprint: {info.get('fingerprint')}")
        if info.get("name"):
            print(f"  creator:     {info['name']}")
        print("Run `mememage profile switch " + info['id'] + "` to start signing with it.")
    elif sub == "alias":
        try:
            record = profiles.sign_alias(args.profile_id)
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            print(f"\u2717 {e}")
            sys.exit(1)
        # Upload to active key's keychain so verifiers can discover the link.
        chain_id = signing.keychain_identifier(record["signer_fingerprint"])
        clean = record["alias_fingerprint"].replace(":", "")
        filename = f"alias-{clean}.json"
        try:
            signing.upload_keychain_record(record, chain_id, filename)
        except Exception as e:
            print(f"\u2717 Upload failed: {e}")
            print("Record was signed but not published. Retry with the same command later.")
            sys.exit(1)
        print(f"Alias published: active profile ({record['signer_fingerprint']}) ↔ {args.profile_id} ({record['alias_fingerprint']})")
        print(f"  uploaded to:  {chain_id}/{filename}")
        print(f"  tip: run the same command from {args.profile_id!r} to publish the reverse alias")
        print(f"       (verifiers treat bidirectional aliases as a stronger signal).")
    elif sub == "remove":
        try:
            res = profiles.remove(args.profile_id)
        except (FileNotFoundError, ValueError) as e:
            print(f"\u2717 {e}")
            sys.exit(1)
        print(f"Archived profile {args.profile_id!r} to {res['archived']}")
        print("Old records signed by this profile's key still verify.")
    else:
        print("Usage: mememage profile {list|active|switch|new|import|alias|remove}")
        sys.exit(1)


def cmd_tls(args):
    """Generate a self-signed TLS cert for the local mint server.

    Real-cert paths (Let's Encrypt, etc.) need a domain you control;
    for IP-only deployments and quick test setups, self-signed gets
    you past the secure-context requirement (phone GPS capture,
    Web Crypto in the validator). Browsers will warn on first visit —
    accept once, the cert is good for ~10 years.

    Writes ``mememage.crt`` + ``mememage.key`` to ``~/.mememage/certs/``
    and updates ``~/.mememage/server.json`` to point at them.
    """
    from mememage import tls
    if not args.self_signed:
        print("Only --self-signed is supported today.")
        print("For Let's Encrypt-issued certs use certbot directly + point the")
        print("server config at /etc/letsencrypt/live/<domain>/fullchain.pem +")
        print("privkey.pem via Config \u2192 Server in the dashboard.")
        sys.exit(1)
    try:
        result = tls.generate_self_signed(
            hostname=args.hostname,
            ip_address=args.ip,
        )
    except RuntimeError as e:
        print(f"\u2717 {e}")
        sys.exit(1)
    print(f"Generated self-signed cert for {result['common_name']}")
    print(f"  Cert:        {result['cert']}")
    print(f"  Key:         {result['key']}")
    print(f"  Alt names:   {', '.join(str(n) for n in result['alt_names'])}")
    print(f"  Fingerprint: SHA-256 {result['fingerprint']}")
    print(f"  Expires:     {result['expires']}")
    # Stamp it into server.json automatically. Domain only set when an
    # explicit hostname was given — for raw-IP setups, leave domain
    # unset and let the server pick it up from the Host header.
    tls.update_server_config(
        result["cert"], result["key"], domain=args.hostname,
    )
    print()
    print(f"server.json updated. Restart the mint server to pick up the new cert:")
    if sys.platform == "darwin":
        print(f"  launchctl kickstart -k \"gui/$UID/com.mememage.mint\"")
    else:
        print(f"  systemctl --user restart mememage-mint")
    print()
    print("Browsers will warn on first visit (self-signed). Accept once;")
    print(f"the fingerprint above is what you're confirming.")


def cmd_install(args):
    """Install Mememage as a user service (LaunchAgent / systemd-user)."""
    from mememage import install as _install
    _install.install(port=args.port)


def cmd_uninstall(args):
    """Stop and remove the Mememage user service."""
    from mememage import install as _install
    _install.uninstall()


def cmd_status(args):
    """Show service install + server reachability status."""
    from mememage import install as _install
    _install.status(port=args.port)


def cmd_token(args):
    """Manage MINT_API_TOKEN — the dashboard / admin-endpoint bearer.

    Word-phrase tokens are easier to read, dictate, and not mistake
    for an attack surface than hex blobs. Old hex tokens keep working;
    this only changes what gets generated, not what's accepted.
    """
    import os
    from pathlib import Path
    from mememage.config import _load_dotenv
    from mememage.tokens import generate_word_token, looks_like_word_token

    sub = getattr(args, "token_sub", None)
    if sub == "show" or sub is None:
        _load_dotenv()
        cur = os.environ.get("MINT_API_TOKEN", "")
        if not cur:
            print("MINT_API_TOKEN is unset.")
            print("  Localhost-only mode: dashboard + admin endpoints are open.")
            print("  Generate one with: mememage token new --write")
            return
        kind = "word phrase" if looks_like_word_token(cur) else "opaque string"
        print(f"MINT_API_TOKEN ({kind}):")
        print(f"  {cur}")
        return

    if sub == "new":
        words = max(4, int(args.words))
        token = generate_word_token(words)
        print(token)
        if not args.write:
            print()
            print("Token NOT saved. Copy it into your .env (or pass --write).")
            print("  echo \"MINT_API_TOKEN=$TOKEN\" >> .env")
            return

        env_path = Path(__file__).resolve().parent.parent / ".env"
        # Line-by-line rewrite: preserve comments + other keys, replace
        # MINT_API_TOKEN if present, append if not. Mirrors the
        # /api/config/env writer in server.py.
        lines = []
        if env_path.exists():
            lines = env_path.read_text(encoding="utf-8").splitlines()
        new_lines = []
        replaced = False
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                new_lines.append(line)
                continue
            k, _, _ = stripped.partition("=")
            if k.strip() == "MINT_API_TOKEN":
                new_lines.append(f"MINT_API_TOKEN={token}")
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f"MINT_API_TOKEN={token}")
        env_path.write_text(
            "\n".join(new_lines) + ("\n" if new_lines else ""),
            encoding="utf-8",
        )
        print()
        print(f"Saved to {env_path}")
        print("Restart the mint server to pick it up:")
        print("  launchctl kickstart -k gui/$(id -u)/com.mememage.mint   # macOS")
        print("  systemctl --user restart mememage-mint                  # Linux")
        return

    print("Usage: mememage token {show|new [--words N] [--write]}")
    sys.exit(1)


def cmd_payload(args):
    """Manage the Payload/ staging directory — source of truth for what gets sealed."""
    from mememage import payload

    sub = getattr(args, "payload_sub", None)
    if sub == "build":
        manifest = payload.build()
        print(f"Payload built: {manifest['built_at']}")
        print(f"  {len(manifest['artifacts'])} artifacts staged in {payload.payload_dir()}")
        print(f"  docs/standalone.html mirrored")
    elif sub == "status":
        payload.print_status()
    elif sub == "diff":
        payload.print_diff()
    elif sub == "inspect":
        if not args.artifact:
            print("Specify an artifact name. Use `mememage payload status` to list them.")
            sys.exit(1)
        payload.inspect(args.artifact)
    else:
        print("Usage: mememage payload {build|status|diff|inspect <artifact>}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(prog="mememage", description="Mememage CLI")
    sub = parser.add_subparsers(dest="command")

    # keygen
    p_keygen = sub.add_parser("keygen", help="Generate Ed25519 signing key pair")
    p_keygen.add_argument("--name", help="Creator name (embedded in every signed record)")
    p_keygen.add_argument("--force", action="store_true", help="Overwrite existing key")

    # rotate
    p_rotate = sub.add_parser("rotate", help="Rotate to a new signing key")
    p_rotate.add_argument("--name", help="New creator name (keeps old if omitted)")
    p_rotate.add_argument("--local-only", action="store_true", help="Don't upload succession record")

    # revoke
    p_revoke = sub.add_parser("revoke", help="Publish revocation certificate (emergency)")
    p_revoke.add_argument("--yes", action="store_true", help="Skip confirmation prompt")

    # verify
    p_verify = sub.add_parser("verify", help="Verify an image's provenance")
    p_verify.add_argument("image", help="Path to image file")

    # seal — the raw headline API: stamp a bar + build the soul
    p_seal = sub.add_parser(
        "encode",
        help="Encode a bar + build a record from your fields (core API)")
    p_seal.add_argument("image", help="PNG image to encode (modified in place)")
    p_seal.add_argument(
        "--field", action="append", metavar="KEY=VALUE",
        help="A record field (repeatable). String values; use --fields for typed/nested.")
    p_seal.add_argument(
        "--fields", metavar="JSON_FILE",
        help="Read record fields from a JSON object file ('-' for stdin)")
    p_seal.add_argument("--prefix", default="mememage",
                        help="Identifier prefix (default: mememage)")
    p_seal.add_argument("--identifier",
                        help="Override the content-addressed identifier")
    p_seal.add_argument("--encrypt", action="store_true",
                        help="Encrypt ALL fields behind a password (private record)")
    p_seal.add_argument("--private", metavar="F1,F2",
                        help="Encrypt only these comma-separated fields (rest public)")
    p_seal.add_argument("--password-env", metavar="VAR",
                        help="Read the encrypt password from this env var (else prompt)")
    p_seal.add_argument("-o", "--out",
                        help="Record output path (default: <identifier>.json beside the image)")

    # decode — read the bar; with --record, verify against a local record file
    p_decode = sub.add_parser(
        "decode", help="Read the bar (identifier + content hash); with --record, verify (core API)")
    p_decode.add_argument("image", help="Image to decode (PNG, JPEG, screenshot)")
    p_decode.add_argument("--record", dest="record", metavar="FILE",
                          help="A local record file (JSON) to verify the image against")
    p_decode.add_argument("--unlock", action="store_true",
                          help="With --record: decrypt the record's private fields (prompts for password)")
    p_decode.add_argument("--password-env", metavar="VAR",
                          help="Read the unlock password from this env var (else prompt)")
    p_decode.add_argument("--json", action="store_true",
                          help="Machine-readable JSON output")

    # forecast — Monte-Carlo predict the rarity distribution for "next mint"
    p_forecast = sub.add_parser(
        "forecast",
        help="Predict next-mint rarity distribution against current conditions",
    )
    p_forecast.add_argument("-n", type=int, default=10000,
        help="Number of simulated mints (default 10000)")

    # validate
    p_val = sub.add_parser("validate", help="Run the full validation suite")
    p_val.add_argument("--quick", action="store_true", help="Skip slow tests")
    p_val.add_argument("--section", type=int, help="Run only this section (1-8)")
    p_val.add_argument("--image", help="Use this image instead of generating one")
    p_val.add_argument("--keep", action="store_true", help="Keep temp files")

    # payload — manage the Payload/ staging directory
    p_payload = sub.add_parser("payload", help="Manage the Payload/ staging directory")
    p_payload_sub = p_payload.add_subparsers(dest="payload_sub")
    p_payload_sub.add_parser("build", help="Regenerate Payload/ from active sources")
    p_payload_sub.add_parser("status", help="Show artifact currency vs sources")
    p_payload_sub.add_parser("diff", help="Show what would change if rebuilt")
    p_payload_inspect = p_payload_sub.add_parser("inspect", help="Preview one artifact")
    p_payload_inspect.add_argument("artifact", nargs="?", help="Artifact name (e.g. truth.md)")

    # serve — start the mint server in the foreground
    p_serve = sub.add_parser("serve", help="Start the mint server (foreground)")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=None,
        help="Listen port (default 8443; a free local port in --local mode)")
    p_serve.add_argument("--cert", default=None, help="TLS cert path (default: ~/.mememage/server.json or ~/.mememage/certs/)")
    p_serve.add_argument("--key", default=None, help="TLS key path")
    p_serve.add_argument("--no-tls", action="store_true", help="Disable TLS (HTTP only)")
    p_serve.add_argument("--local", action="store_true",
        help="Desktop mode: bind 127.0.0.1 over HTTP and open the dashboard "
             "(localhost is a secure context, so GPS capture still works)")
    p_serve.add_argument("--force-open", action="store_true",
        help="Skip the public-domain + no-MINT_API_TOKEN safety pause "
             "(intentional open dev deployments)")

    # app — desktop launcher (loopback HTTP + open dashboard). The
    # command a packaged double-click binary runs.
    p_app = sub.add_parser("app", help="Launch the desktop app (local server + open dashboard)")
    p_app.add_argument("--port", type=int, default=None, help="Listen port (default: a free local port)")
    p_app.add_argument("--host", default="127.0.0.1")
    p_app.add_argument("--cert", default=None)
    p_app.add_argument("--key", default=None)
    p_app.add_argument("--no-tls", action="store_true", default=True)
    p_app.add_argument("--force-open", action="store_true", default=True)
    p_app.set_defaults(func=cmd_app)

    # dashboard — open the dashboard URL in the browser
    p_dash = sub.add_parser("dashboard", help="Open the dashboard URL in your browser")
    p_dash.add_argument("--host", default=None, help="Server host (default: localhost or server.json domain)")
    p_dash.add_argument("--port", type=int, default=None, help="Server port (default: 8443)")
    p_dash.add_argument("--no-tls", action="store_true", help="Use http:// instead of https://")

    # install — set up the user service so the server runs on login
    p_tls = sub.add_parser("tls", help="Generate / install TLS cert for the mint server")
    p_tls.add_argument("--self-signed", action="store_true",
        help="Generate a self-signed RSA-2048 cert valid 10 years")
    p_tls.add_argument("--hostname", default=None,
        help="DNS name to put in the cert SAN (auto-detected if omitted)")
    p_tls.add_argument("--ip", default=None,
        help="IP address to put in the cert SAN (use for IP-only deployments)")

    p_install = sub.add_parser("install", help="Install Mememage as a user service (runs on every login)")
    p_install.add_argument("--port", type=int, default=8443, help="Port to bind (default: 8443)")

    # uninstall — remove the user service
    sub.add_parser("uninstall", help="Stop and remove the user service")

    # status — service + server reachability
    p_status = sub.add_parser("status", help="Show service install + server reachability status")
    p_status.add_argument("--port", type=int, default=8443, help="Port to probe (default: 8443)")

    # chain — per-chain management (multi-chain support)
    p_chain = sub.add_parser("chain", help="Manage chains (list / switch / create / migrate)")
    p_chain_sub = p_chain.add_subparsers(dest="chain_sub")
    p_chain_sub.add_parser("current", help="Print the active chain ID")
    p_chain_sub.add_parser("list", help="List all chains")
    p_chain_status = p_chain_sub.add_parser("status", help="Show chain metadata")
    p_chain_status.add_argument("chain_id", nargs="?", help="Chain ID (default: current)")
    p_chain_switch = p_chain_sub.add_parser("switch", help="Set the active chain")
    p_chain_switch.add_argument("chain_id")
    p_chain_new = p_chain_sub.add_parser("new", help="Create a new chain")
    p_chain_new.add_argument("chain_id")
    p_chain_new.add_argument("--name", help="Display name (default: chain_id)")
    p_chain_new.add_argument("--visibility", choices=("light_energy", "dark_matter"),
                              default="light_energy")
    p_chain_remove = p_chain_sub.add_parser("remove",
        help="Permanently delete a chain and free its disk (not recoverable)")
    p_chain_remove.add_argument("chain_id")

    p_chain_reset = p_chain_sub.add_parser("reset",
        help="Reset a chain's mint position to 0 so the next mint becomes "
             "the heart star of a new constellation. Backs up prior state.")
    p_chain_reset.add_argument("chain_id")
    p_chain_reset.add_argument("--clear-records", action="store_true",
        help="Also wipe the chain's local records/ backup directory "
             "(IA-uploaded records still exist, this only clears the local "
             "mirror — useful when rebuilding test chains)")

    p_chain_migrate = p_chain_sub.add_parser("migrate",
        help="One-shot migration of legacy flat state into chains/<id>/")
    p_chain_migrate.add_argument("--chain-id", dest="chain_id", default=None,
        help="ID for the migrated chain (default: aries)")
    p_chain_migrate.add_argument("--name", default=None,
        help="Display name for the migrated chain (default: Age of Aries)")
    p_chain_migrate.add_argument("--visibility", choices=("light_energy", "dark_matter"),
                                  default="light_energy")

    # token — manage MINT_API_TOKEN (the dashboard / admin-endpoint bearer).
    # Word-phrase tokens are more readable than hex blobs and carry
    # comparable entropy.
    p_token = sub.add_parser("token",
        help="Manage MINT_API_TOKEN (the dashboard bearer). Generate a "
             "readable word-phrase token or print the current one.")
    p_token_sub = p_token.add_subparsers(dest="token_sub")
    p_token_sub.add_parser("show", help="Print the current MINT_API_TOKEN")
    p_token_new = p_token_sub.add_parser("new",
        help="Generate a fresh word-phrase token. Prints to stdout; "
             "use --write to also update ~/.mememage/.env.")
    p_token_new.add_argument("--words", type=int, default=12,
        help="Number of words (default 12 = ~108 bits of entropy)")
    p_token_new.add_argument("--write", action="store_true",
        help="Replace MINT_API_TOKEN in ~/.mememage/.env. Without this "
             "flag the token only prints; you copy it yourself.")

    # profile — multi-key identity management (laptop key, VPS key, …)
    # Parallel surface to `chain`; profiles are orthogonal to chains.
    p_profile = sub.add_parser("profile",
        help="Manage signing-key profiles (list / switch / new / import / alias / remove)")
    p_profile_sub = p_profile.add_subparsers(dest="profile_sub")
    p_profile_sub.add_parser("list", help="List all profiles + active marker")
    p_profile_sub.add_parser("active", help="Print the active profile ID")
    p_profile_switch = p_profile_sub.add_parser("switch", help="Set the active profile")
    p_profile_switch.add_argument("profile_id")
    p_profile_new = p_profile_sub.add_parser("new",
        help="Generate a fresh Ed25519 keypair under a new profile")
    p_profile_new.add_argument("profile_id")
    p_profile_new.add_argument("--name", help="Creator display name (embedded in signed records)")
    p_profile_import = p_profile_sub.add_parser("import",
        help="Import an existing Ed25519 private key (PEM / OpenSSH) as a new profile")
    p_profile_import.add_argument("profile_id")
    p_profile_import.add_argument("key_file",
        help="Path to private key file (PEM or OpenSSH format). Use '-' for stdin.")
    p_profile_import.add_argument("--name", help="Creator display name for this profile")
    p_profile_alias = p_profile_sub.add_parser("alias",
        help="Active profile signs an alias record naming another profile and publishes to IA")
    p_profile_alias.add_argument("profile_id",
        help="The OTHER profile to alias the active profile to")
    p_profile_remove = p_profile_sub.add_parser("remove",
        help="Archive a non-active profile (old records still verify)")
    p_profile_remove.add_argument("profile_id")

    args = parser.parse_args()
    if args.command == "keygen":
        cmd_keygen(args)
    elif args.command == "rotate":
        cmd_rotate(args)
    elif args.command == "revoke":
        cmd_revoke(args)
    elif args.command == "verify":
        cmd_verify(args)
    elif args.command == "encode":
        cmd_seal(args)
    elif args.command == "decode":
        cmd_decode(args)
    elif args.command == "forecast":
        from mememage.forecast import forecast, print_forecast
        print_forecast(forecast(n=args.n))
    elif args.command == "validate":
        # Delegate to the validate script
        from tools import validate
        sys.argv = ["validate"] + [a for a in sys.argv[2:]]
        validate.main()
    elif args.command == "payload":
        cmd_payload(args)
    elif args.command == "serve":
        cmd_serve(args)
    elif args.command == "app":
        cmd_app(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)
    elif args.command == "tls":
        cmd_tls(args)
    elif args.command == "install":
        cmd_install(args)
    elif args.command == "uninstall":
        cmd_uninstall(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "chain":
        cmd_chain(args)
    elif args.command == "profile":
        cmd_profile(args)
    elif args.command == "token":
        cmd_token(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
