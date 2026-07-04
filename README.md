# Mememage — Provenance

A self-hostable toolkit for **image provenance** — give any image a tamper-evident mark tied to a record you keep, so anyone can confirm where it came from and that it hasn't been altered from any copy. It's built on [mememage](https://github.com/sememtac/mememage): a 2-pixel bar links an image to a JSON record, verified by hash alone. This repo bundles the full stack around that core — a **mint server** to stamp and publish images, and a web **decoder** and **validator** to read and verify them.

Beyond the identifier and hash, the included reference chain can capture a rich snapshot of the moment an image is published:

- **Celestial birth certificate** — sun, moon, and planet positions, computed from Meeus' astronomical algorithms.
- **Machine reading** — a cross-platform (macOS/Linux) hardware and live-state snapshot of the publishing machine: cores, memory, load, kernel entropy.
- **Rarity** — a score from the celestial state, the machine vitals, and raw entropy.
- **Time-locked GPS** — the coordinate, behind an RSA time-lock puzzle (sequential squaring, ~10 years).
- **Lineage** — a `parent_id` linked list chaining every image back to a genesis.

Beyond the snapshot, the record carries creator-defined fields — caption, credit, license — entered in the mint editor or prefilled from an image's EXIF (camera, lens, date).

## What's here

- **`mememage/`** — the library: the `encode` / `decode` / `verify` API and bar codec, plus the canonical-chain implementation (celestial certificate, rarity, Ed25519 signing, distribution channels, mint server).
- **`docs/`** — the web **decoder** (light) and **validator** (dark). Drop an image, read the bar, fetch the record, verify by hash. Vanilla HTML/CSS/JS, no build step.
- **`tools/`** — stand up a public mint server on a fresh box (`bootstrap.sh`, `vps-setup.sh`) and build the desktop app.

## Install

**Desktop app** — download the build for your platform from [Releases](https://github.com/sememtac/mememage-provenance/releases). Each bundles the mint server and the full web UI; no Python needed.

- **macOS** — `Mememage.app` (double-click in Finder)
- **Windows** — `Mememage.exe`
- **Linux** — the `Mememage` binary

**From source** — any platform with Python 3.10+:

```bash
git clone https://github.com/sememtac/mememage-provenance
cd mememage-provenance
pip install ".[mint]"     # the full mememage + Pillow, numpy, qrcode
mememage serve            # HTTPS mint server + dashboard
```

The `mememage` package here is the full library — the core API plus the canonical chain — so there's no separate `pip install mememage`. The mint server serves the decoder and validator itself, or host `docs/` on any static surface. For a public server on a fresh Ubuntu/Debian box, `tools/bootstrap.sh` sets up TLS + nginx in one command.

## Built on

- **mememage** — the bar/record/hash technique underneath everything.
- **Python standard library** — the mint server, the chain, the RSA time-lock, and the ephemeris math run on stdlib alone.
- **Pillow** (image/bar), **cryptography** (Ed25519 + AES), **numpy** (optional watermark) — lazy-loaded extras.
- **Vanilla HTML/CSS/JS** — the decoder and validator are static pages.

## Payload

A chain distributes files across its own records. Each layer in `chain.json` names a source and a cadence — the number of records the source spreads across before the cycle repeats. At seal time the source is split into that many chunks, one per record. Reassembly walks the lineage, collects a cycle's chunks, verifies them against the per-chunk hashes in the records, and rebuilds the file. When the decoder and validator are among the distributed files, a chain can be read without external hosting.

Layers and their sources are defined in `chain.json`, pointing at a `payload/` directory; `ChainConfig.default()` is an example configuration. The repo provides the mechanism, not a configured chain.

## License

MIT.
